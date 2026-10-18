"""The inbox-sorter command line. The only module that imports Typer (C-thin-cli)."""

import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from inbox_sorter import runner
from inbox_sorter.config import load_config, resolve_config_path
from inbox_sorter.engines import make_engine
from inbox_sorter.errors import UserError, error_reason
from inbox_sorter.gmail import GmailMailbox
from inbox_sorter.questions import build_questions
from inbox_sorter.storage import state_dir
from inbox_sorter.ui.plain import PlainReporter
from inbox_sorter.ui.table import TableReporter

# Plain-text help and errors; Rich is reserved for the run dashboard.
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
    cfg = load_config(resolve_config_path(config))
    questions = build_questions(cfg)  # fails on a bad config before the model load
    # Sign-in and the model load may print (the OAuth prompt does); keep
    # stdout for the reporter's records (C-plain-output).
    with redirect_stdout(sys.stderr):
        mailbox = GmailMailbox.connect(state_dir())
        print("loading the clef model…", flush=True)
        try:
            engine = make_engine("clef-local")  # A-fixed-engine
        except (OSError, RuntimeError) as error:
            raise UserError(f"could not load the clef model: {error_reason(error)}") from error

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


def main() -> None:
    """Console entry point: report a UserError as one line and exit 1 (C-clear-errors)."""
    try:
        app()
    except UserError as error:
        if "--debug" in sys.argv[1:]:
            raise
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
