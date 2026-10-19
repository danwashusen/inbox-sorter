"""Plain mode: one stable record per line, as logfmt or JSON lines (ux.md §Plain mode).

``run`` writes one per event; ``setup`` writes its accuracy records through ``record``.

No email body is ever printed; output is often piped to a file.
"""

import json
import unicodedata
from typing import Any, TextIO

from inbox_sorter.types import (
    MessageDecided,
    MessageFailed,
    Retrying,
    RunEvent,
    RunFinished,
    RunStarted,
    RunStopped,
)

# Escapes inside a quoted logfmt value; other unsafe characters become \uXXXX.
ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
# Bidi controls can reorder what a terminal shows (sender spoofing).
BIDI_CONTROLS = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
# A progress record follows every 10th message, except the last (ux.md §Plain mode).
PROGRESS_EVERY = 10


def plain_line(event: RunEvent, json_lines: bool) -> str:
    """The record for one run event."""
    match event:
        case RunStarted():
            name = "started"
            fields: dict[str, Any] = {
                "run": event.run_id,
                "mode": "dry-run" if event.dry_run else "apply",
                "engine": event.engine,
                "total": event.total,
            }
        case MessageDecided(email=email, decision=decision):
            name = "decided"
            fields = {
                "id": decision.message_id,
                "category": decision.category,
                "confidence": round(decision.confidence, 2),
                "reply": decision.needs_reply,
                "today": decision.today,
                "low": decision.low_confidence,
                "body": decision.used_body,
                "add": list(decision.add_labels),
                "remove": list(decision.remove_labels),
                "sender": email.sender,
                "subject": email.subject,
            }
        case MessageFailed():
            name = "failed"
            fields = {"id": event.message_id, "error": event.error}
        case RunFinished():
            name = "finished"
            fields = {"run": event.run_id, "decided": event.decided, "failed": event.failed}
        case Retrying():
            name = "retrying"
            fields = {
                "id": event.message_id,  # None, printed empty, while listing
                "attempt": event.attempt,
                "of": event.attempts,
                "delay": event.delay,
                "error": event.error,
            }
        case RunStopped():
            name = "stopped"
            fields = {
                "run": event.run_id,
                "decided": event.decided,
                "failed": event.failed,
                "remaining": event.remaining,
            }
    return record(name, fields, json_lines)


def progress_line(done: int, total: int | None, json_lines: bool) -> str:
    """The periodic progress record; not a RunEvent."""
    return record("progress", {"done": done, "total": total}, json_lines)


class PlainReporter:
    """Writes each event's record, plus a progress record every ``progress_every`` messages."""

    def __init__(
        self, out: TextIO, json_lines: bool = False, progress_every: int = PROGRESS_EVERY
    ) -> None:
        self.out = out
        self.json_lines = json_lines
        self.progress_every = progress_every
        self.done = 0
        self.total: int | None = None

    def __call__(self, event: RunEvent) -> None:
        self._write(plain_line(event, self.json_lines))
        if isinstance(event, RunStarted):
            self.done, self.total = 0, event.total
        elif isinstance(event, MessageDecided | MessageFailed):
            self.done += 1
            # The last message is followed by "finished", not progress.
            if self.done % self.progress_every == 0 and self.done != self.total:
                self._write(progress_line(self.done, self.total, self.json_lines))

    def _write(self, line: str) -> None:
        print(line, file=self.out, flush=True)


def record(name: str, fields: dict[str, Any], json_lines: bool) -> str:
    """One record: ``name`` then ``fields`` in order, as logfmt or a JSON line."""
    if json_lines:
        line = json.dumps({"event": name, **fields}, ensure_ascii=False)
        # json leaves these raw, but many line readers split on them.
        return "".join(f"\\u{ord(char):04x}" if unsafe_char(char) else char for char in line)
    return " ".join([name, *(f"{key}={_logfmt(value)}" for key, value in fields.items())])


def _logfmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    if value is None:
        text = ""
    elif isinstance(value, list):
        text = ",".join(value)
    else:
        text = str(value)
    if text and not any(char in ' "=\\' or unsafe_char(char) for char in text):
        return text
    return '"' + "".join(_escape(char) for char in text) + '"'


def _escape(char: str) -> str:
    if char in ESCAPES:
        return ESCAPES[char]
    return f"\\u{ord(char):04x}" if unsafe_char(char) else char


def unsafe_char(char: str) -> bool:
    """A character that could split a record or restyle a terminal: a control
    character, a line or paragraph separator, or a bidi control."""
    return unicodedata.category(char) in ("Cc", "Zl", "Zp") or char in BIDI_CONTROLS
