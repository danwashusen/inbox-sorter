from dataclasses import replace

import pytest

from inbox_sorter.gmail import PROCESSED_LABEL
from inbox_sorter.summary import CategoryTotal, RunSummary, SummaryTally
from inbox_sorter.types import (
    Decision,
    MessageDecided,
    MessageFailed,
    Retrying,
    RunFinished,
    RunStarted,
    RunStopped,
)
from tests.factories import ALERTS, RECEIPTS, make_config, make_email

NOTICES = replace(ALERTS, id="notices", gmail_label="Sorter/Notices")
CONFIG = make_config(ALERTS, NOTICES, RECEIPTS)


def decided(message_id, category, add, remove=(), reply=False, today=False, low=False):
    decision = Decision(
        message_id, category, 0.9, reply, today, low, False, (*add, PROCESSED_LABEL), remove
    )
    return MessageDecided(make_email(message_id), decision)


class Clock:
    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


def test_a_tally_totals_categories_in_config_order_and_counts_flags():
    tally = SummaryTally(CONFIG, Clock(100.0, 161.5))
    events = [
        RunStarted("r1", dry_run=False, engine="fake", total=5),
        decided("m1", "receipts", ["Receipts"], ("INBOX", "UNREAD")),
        decided("m2", "receipts", ["Receipts"], ("INBOX",)),
        decided("m3", "receipts", ["Receipts", "Sorter/Needs reply"], reply=True),
        decided("m4", "alerts", ["Sorter/Alerts", "Sorter/Today", "Sorter/_low-confidence"],
                today=True, low=True),
        MessageFailed("m5", "gone"),
        RunFinished("r1", decided=4, failed=1),
    ]  # fmt: skip

    for event in events:
        tally(event)

    assert tally.summary() == RunSummary(
        run_id="r1",
        dry_run=False,
        engine="fake",
        categories=(  # notices had no message, so it is left out
            CategoryTotal("alerts", "Sorter/Alerts", 1, 1, 0, 0),
            CategoryTotal("receipts", "Receipts", 3, 3, 2, 1),
        ),
        decided=4,
        failed=1,
        needs_reply=1,
        today=1,
        low_confidence=1,
        elapsed=61.5,
    )


def test_a_message_without_its_category_label_is_not_counted_as_labelled():
    tally = SummaryTally(CONFIG, Clock(0.0, 1.0))
    tally(RunStarted("r1", dry_run=True, engine="fake", total=1))
    tally(decided("m1", "receipts", [], ("INBOX",)))  # an archive-only category

    [total] = tally.summary().categories

    assert (total.messages, total.labelled, total.archived) == (1, 0, 1)


def test_an_unfinished_run_measures_elapsed_to_now():
    tally = SummaryTally(CONFIG, Clock(10.0, 12.0, 15.0))
    tally(RunStarted("r1", dry_run=True, engine="fake", total=None))

    assert tally.summary().elapsed == 2.0
    assert tally.summary().elapsed == 5.0


def test_summary_before_the_run_starts_is_an_error():
    tally = SummaryTally(CONFIG)

    assert not tally.started
    with pytest.raises(ValueError, match="no run has started"):
        tally.summary()
    tally(RunStarted("r1", dry_run=True, engine="fake", total=0))
    assert tally.started


def test_a_stopped_run_ends_the_clock_and_a_retry_changes_no_count():
    tally = SummaryTally(CONFIG, Clock(10.0, 14.0, 99.0))
    tally(RunStarted("r1", dry_run=False, engine="fake", total=3))
    tally(decided("m1", "receipts", ("Receipts",)))
    tally(Retrying("m2", 1, 10, 1.0, "Gmail request failed (503): busy"))
    tally(RunStopped("r1", decided=1, failed=0, remaining=2))

    summary = tally.summary()
    assert summary.elapsed == 4.0
    assert (summary.decided, summary.failed) == (1, 0)
