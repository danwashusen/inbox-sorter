"""The run log: one JSON line per applied decision in runs.jsonl (R-run-log).

Each line is written and synced before its Gmail change is made, so undo
never misses a change; ``read_log`` reads the lines back for ``undo``. No
sender, subject, snippet or body is logged.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from inbox_sorter.errors import UserError, error_reason
from inbox_sorter.types import SYSTEM_LABELS, Decision

RUN_LOG_NAME = "runs.jsonl"


class RunLogError(UserError):
    """The run log cannot be read or written."""


def log_line(run_id: str, engine: str, decision: Decision, added: Mapping[str, str]) -> str:
    """One decision as a JSON object, without the newline.

    ``added`` maps each label name this run actually adds to its ID, in
    ``add_labels`` order.
    """
    probabilities = decision.probabilities
    return json.dumps(
        {
            "run": run_id,
            "engine": engine,
            "id": decision.message_id,
            "category": decision.category,
            "confidence": decision.confidence,
            "probabilities": asdict(probabilities) if probabilities is not None else None,
            "reply": decision.needs_reply,
            "today": decision.today,
            "low": decision.low_confidence,
            "body": decision.used_body,
            "add": list(added),
            "add_ids": list(added.values()),
            "remove": list(decision.remove_labels),
        },
        ensure_ascii=False,
    )


class RunLog:
    """Appends lines to the log at ``path``, created readable only by the user."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(
        self, run_id: str, engine: str, decision: Decision, added: Mapping[str, str]
    ) -> None:
        """Write one line and sync it to disk before returning."""
        line = log_line(run_id, engine, decision, added) + "\n"
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.fchmod(fd, 0o600)  # also tighten a log that already exists
            with os.fdopen(fd, "a", encoding="utf-8") as file:
                file.write(line)
                file.flush()
                os.fsync(file.fileno())
        except OSError as error:
            raise RunLogError(
                f"cannot write the run log {self.path}: {error_reason(error)}"
            ) from error


# The only labels an applied decision removes (triage), so the only ones undo
# adds back. Anything else, such as a hand-edited TRASH, is refused.
RESTORABLE = frozenset({"INBOX", "UNREAD"})


@dataclass(frozen=True)
class LoggedChange:
    """One logged line: what an apply run changed on one message."""

    run_id: str
    message_id: str
    added: dict[str, str]  # each label name the run added to its ID, in log order
    removed: tuple[str, ...]  # the system labels the run removed


def read_log(path: Path) -> list[LoggedChange]:
    """Every line of the log at ``path``, in file order.

    Fails closed: any line that cannot be read raises RunLogError, as undo
    needs every line to tell which changes a later run superseded. Blank
    lines and keys undo doesn't use are ignored.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RunLogError(
            f"no applied run has been logged yet: {path} does not exist"
        ) from error
    except (OSError, UnicodeDecodeError) as error:
        raise RunLogError(f"cannot read the run log {path}: {error_reason(error)}") from error
    changes = []
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        change = _parse(line)
        if change is None:
            raise RunLogError(
                f"{path} line {number} is not a run-log line; fix or delete it, then undo again"
            )
        changes.append(change)
    return changes


def _parse(line: str) -> LoggedChange | None:
    try:
        fields: Any = json.loads(line)
    except ValueError:
        return None
    if not isinstance(fields, dict):
        return None
    run_id, message_id = fields.get("run"), fields.get("id")
    add, add_ids, remove = fields.get("add"), fields.get("add_ids"), fields.get("remove")
    if not (isinstance(run_id, str) and isinstance(message_id, str)):
        return None
    if not all(_strings(value) for value in (add, add_ids, remove)):
        return None
    if len(add) != len(add_ids) or not set(remove) <= RESTORABLE:
        return None
    if SYSTEM_LABELS & set(add_ids):  # the writer adds only user labels; undo never strips INBOX
        return None
    added = dict(zip(add, add_ids, strict=True))
    if len(added) != len(add):  # the writer logs each name once
        return None
    return LoggedChange(run_id, message_id, added, tuple(remove))


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
