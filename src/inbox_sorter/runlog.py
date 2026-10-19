"""The run log: one JSON line per applied decision in runs.jsonl (R-run-log).

Each line is written and synced before its Gmail change is made, so undo
never misses a change. No sender, subject, snippet or body is logged.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from inbox_sorter.errors import UserError, error_reason
from inbox_sorter.types import Decision

RUN_LOG_NAME = "runs.jsonl"


class RunLogError(UserError):
    """The run log cannot be written."""


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
