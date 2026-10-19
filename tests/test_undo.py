import pytest

from inbox_sorter.gmail import PROCESSED_LABEL, LabelInfo, MailboxError
from inbox_sorter.runlog import LoggedChange
from inbox_sorter.types import NEEDS_REPLY_LABEL
from inbox_sorter.undo import (
    UndoOutcome,
    UndoPlan,
    UndoStep,
    UnknownRunError,
    apply_undo,
    plan_undo,
)
from tests.factories import make_email
from tests.fake_mailbox import FakeMailbox

LABELS = [
    LabelInfo("Label_5", "Receipts", system=False, message_count=0),
    LabelInfo("Label_7", NEEDS_REPLY_LABEL, system=False, message_count=0),
    LabelInfo("Label_9", PROCESSED_LABEL, system=False, message_count=0),
]
ADDED = {"Receipts": "Label_5", NEEDS_REPLY_LABEL: "Label_7", PROCESSED_LABEL: "Label_9"}
SORTED = ("Label_5", "Label_7", "Label_9")


def change(run_id, message_id, added=None, removed=("INBOX", "UNREAD")):
    return LoggedChange(run_id, message_id, ADDED if added is None else added, removed)


def test_an_unknown_run_is_refused():
    with pytest.raises(UnknownRunError, match="^no applied run r9 in the run log; dry runs"):
        plan_undo([change("r1", "m1")], "r9")


def test_only_the_runs_own_lines_become_steps():
    plan = plan_undo([change("r1", "m1"), change("r2", "m2"), change("r1", "m3")], "r1")

    assert [step.message_id for step in plan.steps] == ["m1", "m3"]
    assert plan.steps[0] == UndoStep("m1", ("INBOX", "UNREAD"), ADDED)
    assert plan.later_run == () and plan.unchanged == ()


def test_a_later_line_from_another_run_supersedes_the_message():
    plan = plan_undo([change("r1", "m1"), change("r1", "m2"), change("r2", "m1")], "r1")

    assert [step.message_id for step in plan.steps] == ["m2"]
    assert plan.later_run == ("m1",)


def test_an_earlier_runs_line_never_supersedes():
    plan = plan_undo([change("r1", "m1"), change("r2", "m1")], "r2")

    assert [step.message_id for step in plan.steps] == ["m1"]
    assert plan.later_run == ()


def test_one_runs_lines_for_a_message_merge_into_one_step():
    lines = [
        change("r1", "m1", {"Receipts": "Label_5"}, ("INBOX",)),
        change("r1", "m1", {PROCESSED_LABEL: "Label_9"}, ("UNREAD", "INBOX")),
    ]

    plan = plan_undo(lines, "r1")

    assert plan.steps == (
        UndoStep("m1", ("INBOX", "UNREAD"), {"Receipts": "Label_5", PROCESSED_LABEL: "Label_9"}),
    )
    assert plan.later_run == ()


def test_a_line_with_nothing_to_reverse_is_unchanged():
    plan = plan_undo([change("r1", "m1", {}, ()), change("r1", "m2")], "r1")

    assert [step.message_id for step in plan.steps] == ["m2"]
    assert plan.unchanged == ("m1",)


def test_a_superseded_empty_line_is_only_later_run():
    plan = plan_undo([change("r1", "m1", {}, ()), change("r2", "m1")], "r1")

    assert plan.later_run == ("m1",) and plan.unchanged == () and plan.steps == ()


def test_each_list_follows_the_messages_first_line():
    lines = [
        change("r1", "m3"),
        change("r1", "m1", {}, ()),
        change("r1", "m4"),
        change("r1", "m2", {}, ()),
        change("r1", "m5"),
        change("r1", "m6"),
        change("r2", "m6"),
        change("r2", "m5"),
        change("r1", "m4"),
    ]

    plan = plan_undo(lines, "r1")

    assert [step.message_id for step in plan.steps] == ["m3", "m4"]
    assert plan.unchanged == ("m1", "m2")
    assert plan.later_run == ("m5", "m6")


def archived(*ids):
    """A mailbox whose messages carry what one sorting run gave them."""
    return FakeMailbox([make_email(id, labels=SORTED) for id in ids], labels=LABELS)


def test_apply_restores_the_removed_labels_and_removes_the_added_ones():
    mailbox = archived("m1")
    plan = plan_undo([change("r1", "m1")], "r1")

    outcomes = list(apply_undo(mailbox, plan))

    [(message_id, add, remove)] = mailbox.modify_calls
    assert (message_id, add) == ("m1", ("INBOX", "UNREAD"))
    assert remove == SORTED
    assert "Label_9" in remove  # Sorter/_processed goes, so the next run sorts it again
    names = ("Receipts", NEEDS_REPLY_LABEL, PROCESSED_LABEL)
    assert outcomes == [UndoOutcome("m1", "undone", ("INBOX", "UNREAD"), names)]
    assert mailbox.emails[0].label_ids == {"INBOX", "UNREAD"}


def test_a_label_deleted_since_the_run_is_not_sent():
    mailbox = archived("m1")
    mailbox.labels = [label for label in LABELS if label.id != "Label_7"]

    [outcome] = apply_undo(mailbox, plan_undo([change("r1", "m1")], "r1"))

    assert mailbox.modify_calls == [("m1", ("INBOX", "UNREAD"), ("Label_5", "Label_9"))]
    assert outcome.removed == ("Receipts", PROCESSED_LABEL)


def test_a_step_left_empty_makes_no_change_and_counts_as_undone():
    mailbox = archived("m1")
    mailbox.labels = []

    outcomes = list(apply_undo(mailbox, plan_undo([change("r1", "m1", removed=())], "r1")))

    assert mailbox.modify_calls == []
    assert outcomes == [UndoOutcome("m1", "undone", (), ())]


def test_a_deleted_message_is_gone_and_undo_carries_on():
    mailbox = archived("m2")  # m1 was deleted since the run

    outcomes = list(apply_undo(mailbox, plan_undo([change("r1", "m1"), change("r1", "m2")], "r1")))

    assert outcomes[0] == UndoOutcome("m1", "gone", (), ())
    assert (outcomes[1].message_id, outcomes[1].status) == ("m2", "undone")
    assert [call[0] for call in mailbox.modify_calls] == ["m1", "m2"]


def test_any_other_gmail_error_ends_undo():
    mailbox = archived("m1", "m2")
    mailbox.fail_modify = {"m1"}
    outcomes = apply_undo(mailbox, plan_undo([change("r1", "m1"), change("r1", "m2")], "r1"))

    with pytest.raises(MailboxError, match="modify failed"):
        list(outcomes)
    assert [call[0] for call in mailbox.modify_calls] == ["m1"]


def test_labels_are_listed_once_and_not_at_all_for_an_empty_plan():
    mailbox = archived("m1", "m2")

    list(apply_undo(mailbox, plan_undo([change("r1", "m1"), change("r1", "m2")], "r1")))
    assert mailbox.label_ids_calls == 1

    empty = FakeMailbox(labels=LABELS)
    assert list(apply_undo(empty, UndoPlan("r1", (), ("m1",), ()))) == []
    assert empty.label_ids_calls == 0 and empty.modify_calls == []
