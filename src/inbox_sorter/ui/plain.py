"""Plain mode: one stable record per event, as logfmt or JSON lines (ux.md §Plain mode).

No email body is ever printed; output is often piped to a file.
"""

import json
import unicodedata
from typing import Any, TextIO

from inbox_sorter.types import MessageDecided, MessageFailed, RunEvent, RunFinished, RunStarted

# Escapes inside a quoted logfmt value; other control characters become \uXXXX.
ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}


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
    return _record(name, fields, json_lines)


def progress_line(done: int, total: int | None, json_lines: bool) -> str:
    """The periodic progress record; not a RunEvent."""
    return _record("progress", {"done": done, "total": total}, json_lines)


class PlainReporter:
    """Writes each event's record, plus a progress record every ``progress_every`` messages."""

    def __init__(self, out: TextIO, json_lines: bool = False, progress_every: int = 10) -> None:
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


def _record(name: str, fields: dict[str, Any], json_lines: bool) -> str:
    if json_lines:
        return json.dumps({"event": name, **fields}, ensure_ascii=False)
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
    if text and not any(char in ' "=\\' or _is_control(char) for char in text):
        return text
    return '"' + "".join(_escape(char) for char in text) + '"'


def _escape(char: str) -> str:
    if char in ESCAPES:
        return ESCAPES[char]
    return f"\\u{ord(char):04x}" if _is_control(char) else char


def _is_control(char: str) -> bool:
    # Line and paragraph separators would also split a record in some readers.
    return unicodedata.category(char) in ("Cc", "Zl", "Zp")
