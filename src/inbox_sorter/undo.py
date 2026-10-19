"""Undo: reverse one applied run from the run log (R-undo).

``plan_undo`` works out, from the log alone, which of a run's changes are
still its own; ``apply_undo`` reverses them, one Gmail change per message.
Undo writes nothing to the log and never creates or deletes a label.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from inbox_sorter.errors import UserError
from inbox_sorter.gmail import Mailbox, MailboxError
from inbox_sorter.runlog import LoggedChange


class UnknownRunError(UserError):
    """No line in the run log has the run ID undo was given."""


@dataclass(frozen=True)
class UndoStep:
    """The reverse of everything one run did to one message."""

    message_id: str
    restore: tuple[str, ...]  # the system labels to add back
    remove: dict[str, str]  # each label name to remove to its logged ID


@dataclass(frozen=True)
class UndoPlan:
    """A run's steps, and the message IDs skipped, each in log order."""

    run_id: str
    steps: tuple[UndoStep, ...]
    later_run: tuple[str, ...]  # a later line from another run supersedes this run's
    unchanged: tuple[str, ...]  # this run added and removed nothing


@dataclass(frozen=True)
class UndoOutcome:
    """What ``apply_undo`` did to one message; a gone message has empty labels."""

    message_id: str
    status: Literal["undone", "gone"]
    restored: tuple[str, ...]
    removed: tuple[str, ...]  # the label names actually sent


def plan_undo(changes: Sequence[LoggedChange], run_id: str) -> UndoPlan:
    """The steps that reverse ``run_id``, from the whole log in file order.

    A message is superseded, and skipped, when a later line for it has a
    different run ID: that run's labels may be the ones it now carries. All
    of this run's lines for one message merge into one step.
    """
    restore: dict[str, dict[str, None]] = {}  # message ID to its labels, as ordered sets
    remove: dict[str, dict[str, str]] = {}
    superseded: set[str] = set()
    for change in changes:
        if change.run_id == run_id:
            restore.setdefault(change.message_id, {}).update(dict.fromkeys(change.removed))
            remove.setdefault(change.message_id, {}).update(change.added)
        elif change.message_id in restore:
            superseded.add(change.message_id)
    if not restore:
        raise UnknownRunError(
            f"no applied run {run_id} in the run log; dry runs are not logged, "
            "and an apply run prints its ID in its summary"
        )
    steps, later_run, unchanged = [], [], []
    for message_id, labels in restore.items():
        if message_id in superseded:
            later_run.append(message_id)
        elif labels or remove[message_id]:
            steps.append(UndoStep(message_id, tuple(labels), remove[message_id]))
        else:
            unchanged.append(message_id)
    return UndoPlan(run_id, tuple(steps), tuple(later_run), tuple(unchanged))


def apply_undo(mailbox: Mailbox, plan: UndoPlan) -> Iterator[UndoOutcome]:
    """Reverse each step with one change, yielding its outcome.

    Labels deleted since the run are dropped from the change. A message
    deleted since (a 404) is ``gone``; any other Gmail error ends undo.
    """
    if not plan.steps:
        return
    live = set(mailbox.label_ids().values())
    for step in plan.steps:
        remove = {name: label_id for name, label_id in step.remove.items() if label_id in live}
        if step.restore or remove:  # Gmail refuses a change with nothing in it
            try:
                mailbox.modify(step.message_id, step.restore, tuple(remove.values()))
            except MailboxError as error:
                if error.status != 404:
                    raise
                yield UndoOutcome(step.message_id, "gone", (), ())
                continue
        yield UndoOutcome(step.message_id, "undone", step.restore, tuple(remove))
