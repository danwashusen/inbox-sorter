"""Undo's output: plain lines on a terminal, records in plain mode.

Only message IDs, label names and counts are printed; undo never fetches a
sender or subject.
"""

from collections import Counter
from collections.abc import Iterable, Sequence

from inbox_sorter.runlog import RESTORABLE
from inbox_sorter.ui.plain import record
from inbox_sorter.ui.table import one_line
from inbox_sorter.undo import UndoOutcome, UndoPlan, UndoStep


def skipped_records(plan: UndoPlan, json_lines: bool) -> list[str]:
    """The messages skipped from the log alone: superseded, then unchanged."""
    return [
        record("skipped", {"id": message_id, "reason": reason}, json_lines)
        for reason, message_ids in (("later-run", plan.later_run), ("unchanged", plan.unchanged))
        for message_id in message_ids
    ]


def step_record(step: UndoStep, json_lines: bool) -> str:
    """A dry run's record for one message: what --apply would send."""
    fields = {"id": step.message_id, "restore": list(step.restore), "remove": list(step.remove)}
    return record("planned", fields, json_lines)


def outcome_record(outcome: UndoOutcome, json_lines: bool) -> str:
    """An apply's record for one message."""
    if outcome.status == "gone":
        return record("skipped", {"id": outcome.message_id, "reason": "gone"}, json_lines)
    fields = {
        "id": outcome.message_id,
        "restore": list(outcome.restored),
        "remove": list(outcome.removed),
    }
    return record("undone", fields, json_lines)


def undo_total_record(run_id: str, apply: bool, undone: int, skipped: int, json_lines: bool) -> str:
    """The last record; in a dry run ``undone`` counts what --apply would undo."""
    fields = {
        "run": run_id,
        "mode": "apply" if apply else "dry-run",
        "undone": undone,
        "skipped": skipped,
    }
    return record("undo", fields, json_lines)


def undo_lines(plan: UndoPlan, outcomes: Sequence[UndoOutcome] | None) -> list[str]:
    """The terminal lines; ``outcomes`` is None for a dry run."""
    if outcomes is None:
        changes = [(step.restore, tuple(step.remove)) for step in plan.steps]
        gone = 0
        heading = f"Undo · DRY RUN · run {plan.run_id} · {len(changes)} to undo"
        removed_title, restored_title = "labels to remove", "to restore"
    else:
        undone = [outcome for outcome in outcomes if outcome.status == "undone"]
        changes = [(outcome.restored, outcome.removed) for outcome in undone]
        gone = len(outcomes) - len(undone)
        heading = f"Undo · APPLY · run {plan.run_id} · {len(changes)} undone"
        removed_title, restored_title = "labels removed", "restored"
    skipped = len(plan.later_run) + len(plan.unchanged) + gone
    lines = [f"{heading} · {skipped} skipped"]
    removed = _counts(label for _, names in changes for label in names)
    if removed:
        lines.append(f"{removed_title}: {removed}")
    restored = Counter(label for labels, _ in changes for label in labels)
    restored_text = ", ".join(
        f"{label} {restored[label]}" for label in sorted(RESTORABLE) if restored[label]
    )
    if restored_text:
        lines.append(f"{restored_title}: {restored_text}")
    parts = [
        (len(plan.later_run), "changed by a later run"),
        (len(plan.unchanged), "not changed by this run"),
        (gone, "no longer in Gmail"),
    ]
    if skipped:
        lines.append("skipped: " + ", ".join(f"{n} {text}" for n, text in parts if n))
    if outcomes is not None:
        if changes:
            lines.append("Undone mail has no Sorter/_processed, so the next run sorts it again.")
    elif changes:
        lines.append(f"Nothing was changed. Apply with: inbox-sorter undo {plan.run_id} --apply")
    else:
        lines.append("Nothing to undo.")
    return lines


def _counts(labels: Iterable[str]) -> str:
    """Each label with its message count, in first-seen order."""
    counts = Counter(labels)  # keeps insertion order
    return ", ".join(f"{one_line(label)} {count}" for label, count in counts.items())
