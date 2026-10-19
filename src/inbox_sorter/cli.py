"""The inbox-sorter command line. The only module that imports Typer (C-thin-cli)."""

import os
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from inbox_sorter import runner
from inbox_sorter.config import ConfigError, load_config, resolve_config_path, write_config
from inbox_sorter.engine import DecisionEngine
from inbox_sorter.engines import make_engine
from inbox_sorter.errors import UserError, error_reason
from inbox_sorter.gmail import GmailMailbox
from inbox_sorter.questions import build_questions
# Not `from inbox_sorter import setup`: the setup command below would shadow it.
from inbox_sorter.setup import SetupCancelled, check_accuracy, run_setup, sample_ids
from inbox_sorter.storage import state_dir
from inbox_sorter.ui.accuracy import (
    ACCURACY_STOPPED,
    NO_LABELLED_MAIL,
    AccuracyProgress,
    accuracy_records,
    print_accuracy,
)
from inbox_sorter.ui.plain import PlainReporter
from inbox_sorter.ui.prompts import CANCELLED, LinePrompter, MenuPrompter
from inbox_sorter.ui.table import TableReporter, one_line

# Plain-text help and errors; Rich is reserved for ui/ (the setup report and the run dashboard).
app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False, rich_markup_mode=None)

# Shared by every command that reads the config (A-config-location).
ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="Config file to read. Defaults to inbox-sorter.toml in the current directory.",
    ),
]


@dataclass(frozen=True)
class CliOptions:
    plain: bool  # one line per event, no live panel
    debug: bool  # show tracebacks instead of one-line errors
    json: bool  # plain records as JSON lines


@app.callback()
def options(
    ctx: typer.Context,
    plain: Annotated[
        bool, typer.Option("--plain", help="Print plain lines instead of the live panel.")
    ] = False,
    json: Annotated[
        bool, typer.Option("--json", help="Print plain records as JSON lines; implies --plain.")
    ] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show tracebacks for errors.")] = False,
) -> None:
    """Triage a Gmail inbox with the clef decision model."""
    ctx.obj = CliOptions(plain=plain or json or not sys.stdout.isatty(), debug=debug, json=json)


@app.command()
def run(
    ctx: typer.Context,
    config: ConfigOption = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Decide at most N messages instead of max_messages."),
    ] = None,
) -> None:
    """Show what the sorter would do to unprocessed inbox mail. Changes nothing in Gmail."""
    opts: CliOptions = ctx.obj
    path = resolve_config_path(config)
    cfg = load_config(path)
    try:
        questions = build_questions(cfg)  # fails on a bad config before the model load
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from error  # name the file, as load_config does
    # Sign-in and the model load may print (the OAuth prompt does); keep
    # stdout for the reporter's records (C-plain-output).
    with redirect_stdout(sys.stderr):
        mailbox = GmailMailbox.connect(state_dir())
        engine = _load_engine()

    report = (
        PlainReporter(sys.stdout, json_lines=opts.json)
        if opts.plain
        else TableReporter(sys.stdout)
    )
    try:
        for event in runner.run(
            mailbox, engine, cfg, questions, limit=limit, run_id=runner.new_run_id()
        ):
            report(event)
    finally:
        engine.close()


@app.command()
def setup(ctx: typer.Context, config: ConfigOption = None) -> None:
    """Build the config from your Gmail labels. Reads Gmail only; writes only the config file."""
    opts: CliOptions = ctx.obj
    path = resolve_config_path(config)
    # An invalid existing config, or nowhere to write one, stops setup before
    # sign-in or any prompt.
    existing = load_config(path) if path.exists() else None
    directory = path.resolve().parent  # write_config writes next to a symlink's target
    if not directory.is_dir():
        raise ConfigError(f"{path}: the directory {directory} does not exist")
    if not os.access(directory, os.W_OK):
        raise ConfigError(f"{path}: cannot write to the directory {directory}")

    # Menus need a terminal on stdin as well as stdout; otherwise ask line by line.
    prompter = (
        MenuPrompter(sys.stdout)
        if not opts.plain and sys.stdin.isatty()
        else LinePrompter(sys.stdin, sys.stdout)
    )
    try:  # Ctrl-C during sign-in or between prompts, as well as at one
        with redirect_stdout(sys.stderr):  # the OAuth prompt prints (C-plain-output)
            mailbox = GmailMailbox.connect(state_dir())
        result = run_setup(mailbox, prompter, existing)
    except KeyboardInterrupt:
        raise SetupCancelled(CANCELLED) from None
    if not result.changed:
        if existing is None:
            print("no categories accepted; nothing was written")
        else:
            print(f"{path} is up to date; nothing was written")
    else:
        write_config(path, result.config, existing)
        print(
            f"wrote {path}: {len(result.added)} added, {len(result.disabled)} disabled, "
            f"{result.unchanged} unchanged"
        )
        for category in result.added:
            print(f"+ {category.id} → {one_line(category.gmail_label)}")
        labels = {category.id: category.gmail_label for category in result.config.categories}
        for category_id in result.disabled:
            print(f"- {category_id} (label {one_line(labels[category_id])} was deleted)")
        if not any(category.enabled for category in result.config.categories):
            print("note: no category is enabled; run refuses this config until one is")
    if not any(category.enabled for category in result.config.categories):
        return

    # The accuracy check runs after the write, so a failed model load or a
    # Ctrl-C during it never loses the wizard's answers.
    try:
        samples = sample_ids(mailbox, result.config)
        if not any(samples.values()):
            print(NO_LABELLED_MAIL)  # skip the model load
            return
        questions = build_questions(result.config)
        with redirect_stdout(sys.stderr):  # keep stdout for the report (C-plain-output)
            engine = _load_engine()
        try:
            with AccuracyProgress(opts.plain, opts.json, sys.stdout) as progress:
                report = check_accuracy(
                    mailbox, engine, result.config, questions, samples, progress=progress
                )
        finally:
            engine.close()
    except KeyboardInterrupt:
        raise UserError(ACCURACY_STOPPED) from None
    if opts.plain:
        for line in accuracy_records(report, opts.json):
            print(line)
    else:
        print_accuracy(report, sys.stdout)


def _load_engine() -> DecisionEngine:
    """Load clef (A-fixed-engine); call with stdout redirected, as loading prints."""
    print("loading the clef model…", flush=True)
    try:
        return make_engine("clef-local")
    except (OSError, RuntimeError) as error:
        raise UserError(f"could not load the clef model: {error_reason(error)}") from error


def main() -> None:
    """Console entry point: report a UserError as one line and exit 1 (C-clear-errors)."""
    try:
        app()
    except UserError as error:
        if "--debug" in sys.argv[1:]:
            raise
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
