"""The inbox-sorter command line. The only module that imports Typer (C-thin-cli)."""

import sys
from dataclasses import dataclass
from typing import Annotated

import typer

from inbox_sorter.errors import UserError

# Plain-text help and errors; Rich is reserved for the run dashboard.
app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False, rich_markup_mode=None)


@dataclass(frozen=True)
class CliOptions:
    plain: bool  # one line per event, no live panel
    debug: bool  # show tracebacks instead of one-line errors


@app.callback()
def options(
    ctx: typer.Context,
    plain: Annotated[
        bool, typer.Option("--plain", help="Print plain lines instead of the live panel.")
    ] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show tracebacks for errors.")] = False,
) -> None:
    """Triage a Gmail inbox with the clef decision model."""
    ctx.obj = CliOptions(plain=plain or not sys.stdout.isatty(), debug=debug)


def main() -> None:
    """Console entry point: report a UserError as one line and exit 1 (C-clear-errors)."""
    try:
        app()
    except UserError as error:
        if "--debug" in sys.argv[1:]:
            raise
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
