"""The terminal run table: one row per decision, printed above the dashboard's panel.

Plain text, readable in a monochrome terminal (R-monochrome-safe). No email
body is ever printed.
"""

import unicodedata

from inbox_sorter.gmail import PROCESSED_LABEL
from inbox_sorter.types import (
    Decision,
    MessageDecided,
    MessageFailed,
    Retrying,
    RunEvent,
    RunFinished,
    RunStarted,
    RunStopped,
)
from inbox_sorter.ui.plain import unsafe_char

# (title, width); the actions column takes the rest of the line.
COLUMNS = (("Sender", 20), ("Subject", 36), ("Category", 14), ("Conf", 4))
GAP = "  "


def planned_actions(decision: Decision) -> str:
    """Such as "+Receipts, archive, mark read"; Sorter/_processed is left out."""
    actions = [f"+{one_line(label)}" for label in decision.add_labels if label != PROCESSED_LABEL]
    if "INBOX" in decision.remove_labels:
        actions.append("archive")
    if "UNREAD" in decision.remove_labels:
        actions.append("mark read")
    return ", ".join(actions)


class TableReporter:
    """The header, one row per message, then the finish or stop line.

    ``lines()`` returns them; the dashboard prints them above its panel.
    """

    def __init__(self) -> None:
        self.dry_run = True

    def lines(self, event: RunEvent) -> list[str]:
        """The lines ``event`` writes; the dashboard prints them above its panel."""
        match event:
            case RunStarted():
                self.dry_run = event.dry_run
                mode = "DRY RUN" if event.dry_run else "APPLY"
                actions = "Planned actions" if event.dry_run else "Actions"
                return [
                    f"{mode} · {event.engine} · {event.total} messages · run {event.run_id}",
                    _row([title for title, _ in COLUMNS], actions),
                ]
            case MessageDecided(email=email, decision=decision):
                cells = [
                    email.sender,
                    email.subject,
                    decision.category,
                    f"{round(decision.confidence * 100)}%",
                ]
                return [_row(cells, planned_actions(decision))]
            case MessageFailed():
                return [f"{one_line(event.message_id)}{GAP}failed: {one_line(event.error)}"]
            case RunFinished() if self.dry_run:
                return [
                    f"Dry run finished: {event.decided} planned, {event.failed} failed. "
                    "Gmail was not changed."
                ]
            case RunFinished():
                return [f"Apply finished: {event.decided} changed, {event.failed} failed."]
            case Retrying():
                return [
                    f"retrying in {round(event.delay)}s "
                    f"(attempt {event.attempt} of {event.attempts}): {one_line(event.error)}"
                ]
            case RunStopped() if self.dry_run:
                return [
                    f"Dry run stopped: {event.decided} planned, {event.failed} failed, "
                    f"{event.remaining} not decided. Gmail was not changed."
                ]
            case RunStopped():
                return [
                    f"Stopped: {event.decided} changed, {event.failed} failed, "
                    f"{event.remaining} left. Run the same command again to resume."
                ]
        return []


def _row(cells: list[str], rest: str) -> str:
    fitted = [fit(cell, width) for cell, (_, width) in zip(cells, COLUMNS, strict=True)]
    return GAP.join([*fitted, rest]).rstrip()


def fit(text: str, width: int) -> str:
    """One line, cut with "…" to ``width`` terminal cells and padded to it."""
    text = one_line(text)
    if _cells(text) > width:
        while _cells(text) > width - 1:
            text = text[:-1]
        text += "…"
    return text + " " * (width - _cells(text))


def _cells(text: str) -> int:
    """Terminal cells: wide and fullwidth characters (CJK, most emoji) take two."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def one_line(text: str) -> str:
    """Fold whitespace, and show other unsafe characters as "?".

    Headers come from senders, so a raw escape sequence could restyle or
    rewrite the terminal.
    """
    text = " ".join(text.split())
    return "".join("?" if unsafe_char(char) else char for char in text)
