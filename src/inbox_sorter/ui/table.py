"""The terminal dry-run table: one row per decision, streamed as it arrives.

Plain text, readable in a monochrome terminal (R-monochrome-safe). No email
body is ever printed.
"""

import unicodedata
from typing import TextIO

from inbox_sorter.gmail import PROCESSED_LABEL
from inbox_sorter.types import (
    Decision,
    MessageDecided,
    MessageFailed,
    RunEvent,
    RunFinished,
    RunStarted,
)

# (title, width); Planned actions takes the rest of the line.
COLUMNS = (("Sender", 20), ("Subject", 36), ("Category", 14), ("Conf", 4))
GAP = "  "


def planned_actions(decision: Decision) -> str:
    """Such as "+Receipts, archive, mark read"; Sorter/_processed is left out."""
    actions = [f"+{label}" for label in decision.add_labels if label != PROCESSED_LABEL]
    if "INBOX" in decision.remove_labels:
        actions.append("archive")
    if "UNREAD" in decision.remove_labels:
        actions.append("mark read")
    return ", ".join(actions)


class TableReporter:
    """Writes the header, one row per message, then the finish line."""

    def __init__(self, out: TextIO) -> None:
        self.out = out

    def __call__(self, event: RunEvent) -> None:
        match event:
            case RunStarted():
                self._write(
                    f"DRY RUN · {event.engine} · {event.total} messages · run {event.run_id}"
                )
                self._write(_row([title for title, _ in COLUMNS], "Planned actions"))
            case MessageDecided(email=email, decision=decision):
                cells = [
                    email.sender,
                    email.subject,
                    decision.category,
                    f"{round(decision.confidence * 100)}%",
                ]
                self._write(_row(cells, planned_actions(decision)))
            case MessageFailed():
                self._write(f"{event.message_id}{GAP}failed: {_one_line(event.error)}")
            case RunFinished():
                self._write(
                    f"Dry run finished: {event.decided} planned, {event.failed} failed. "
                    "Gmail was not changed."
                )

    def _write(self, line: str) -> None:
        print(line, file=self.out, flush=True)


def _row(cells: list[str], rest: str) -> str:
    fitted = [_fit(cell, width) for cell, (_, width) in zip(cells, COLUMNS, strict=True)]
    return GAP.join([*fitted, rest]).rstrip()


def _fit(text: str, width: int) -> str:
    """One line, cut with "…" to ``width`` and padded to it."""
    text = _one_line(text)
    if len(text) > width:
        text = text[: width - 1] + "…"
    return text.ljust(width)


def _one_line(text: str) -> str:
    """Fold whitespace, and show other control characters as "?".

    Headers come from senders, so a raw escape sequence could restyle or
    rewrite the terminal.
    """
    text = " ".join(text.split())
    return "".join("?" if unicodedata.category(char) == "Cc" else char for char in text)
