import json

from inbox_sorter.ui.undo import (
    outcome_record,
    skipped_records,
    step_record,
    undo_counts,
    undo_lines,
    undo_total_record,
)
from inbox_sorter.undo import UndoOutcome, UndoPlan, UndoStep

STEPS = (
    UndoStep("m1", ("INBOX", "UNREAD"), {"Sorter/Social": "Label_3", "Sorter/_processed": "L9"}),
    UndoStep("m2", ("INBOX",), {"Receipts": "Label_5", "Sorter/_processed": "L9"}),
)
PLAN = UndoPlan("r1", STEPS, later_run=("m3",), unchanged=("m4", "m5"))


def test_skipped_records_list_later_run_then_unchanged():
    assert skipped_records(PLAN, False) == [
        "skipped id=m3 reason=later-run",
        "skipped id=m4 reason=unchanged",
        "skipped id=m5 reason=unchanged",
    ]


def test_step_and_outcome_records_name_labels_not_ids():
    assert step_record(STEPS[0], False) == (
        "planned id=m1 restore=INBOX,UNREAD remove=Sorter/Social,Sorter/_processed"
    )
    undone = UndoOutcome("m2", "undone", ("INBOX",), ("Receipts",))
    assert outcome_record(undone, False) == "undone id=m2 restore=INBOX remove=Receipts"
    gone = UndoOutcome("m6", "gone", (), ())
    assert outcome_record(gone, False) == "skipped id=m6 reason=gone"


def test_records_keep_their_key_order_as_json():
    planned = json.loads(step_record(STEPS[1], True))
    total = json.loads(undo_total_record("r1", True, 2, 3, True))

    assert list(planned) == ["event", "id", "restore", "remove"]
    assert planned["remove"] == ["Receipts", "Sorter/_processed"]
    assert total == {"event": "undo", "run": "r1", "mode": "apply", "undone": 2, "skipped": 3}
    dry_run = undo_total_record("r1", False, 2, 3, False)
    assert dry_run == "undo run=r1 mode=dry-run undone=2 skipped=3"


def test_dry_run_lines_count_labels_in_first_seen_order():
    assert undo_lines(PLAN, None) == [
        "Undo · DRY RUN · run r1 · 2 to undo · 3 skipped",
        "labels to remove: Sorter/Social 1, Sorter/_processed 2, Receipts 1",
        "to restore: INBOX 2, UNREAD 1",
        "skipped: 1 changed by a later run, 2 not changed by this run",
        "Nothing was changed. Apply with: inbox-sorter undo r1 --apply",
    ]


def test_apply_lines_count_only_undone_messages_and_leave_out_zero_parts():
    plan = UndoPlan("r1", STEPS, later_run=(), unchanged=())
    outcomes = [
        UndoOutcome("m1", "gone", (), ()),
        UndoOutcome("m2", "undone", ("INBOX",), ("Receipts", "Sorter/_processed")),
    ]

    assert undo_lines(plan, outcomes) == [
        "Undo · APPLY · run r1 · 1 undone · 1 skipped",
        "labels removed: Receipts 1, Sorter/_processed 1",
        "restored: INBOX 1",
        "skipped: 1 no longer in Gmail",
        "Undone mail has no Sorter/_processed, so the next run sorts it again.",
    ]


def test_nothing_to_undo_says_so_and_shows_labels_literally():
    assert undo_lines(UndoPlan("r1", (), (), ("m1",)), None) == [
        "Undo · DRY RUN · run r1 · 0 to undo · 1 skipped",
        "skipped: 1 not changed by this run",
        "Nothing to undo.",
    ]
    odd = UndoPlan("r1", (UndoStep("m1", (), {"[bold]x\ny": "L1"}),), (), ())
    assert undo_lines(odd, None)[1] == "labels to remove: [bold]x y 1"


def test_counts_add_gone_messages_to_the_skips():
    gone = UndoOutcome("m1", "gone", (), ())
    undone = UndoOutcome("m2", "undone", ("INBOX",), ())

    assert undo_counts(PLAN, None) == (2, 3, 0)
    assert undo_counts(PLAN, [gone, undone]) == (1, 4, 1)
