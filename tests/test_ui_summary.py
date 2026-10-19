import io
import json
from dataclasses import replace

import pytest

from inbox_sorter.summary import CategoryTotal, RunSummary
from inbox_sorter.ui.summary import (
    category_actions,
    format_elapsed,
    print_summary,
    summary_heading,
    summary_lines,
    summary_records,
)

SUMMARY = RunSummary(
    run_id="20261019-140211",
    dry_run=False,
    engine="clef-local",
    categories=(CategoryTotal("receipts", "Receipts", 1544, 1544, 1500, 0),),
    decided=1544,
    failed=2,
    needs_reply=3,
    today=1,
    low_confidence=7,
    elapsed=125.6,
)


@pytest.mark.parametrize(
    "seconds, text", [(0, "0s"), (59, "59s"), (61, "1m 01s"), (3700, "1h 01m"), (16440, "4h 34m")]
)
def test_format_elapsed(seconds, text):
    assert format_elapsed(seconds) == text


def test_the_heading_names_the_mode_counts_and_elapsed_time():
    assert summary_heading(SUMMARY) == (
        "Summary · APPLY · 1,544 decided · 2 failed · elapsed 2m 05s"
    )


def test_actions_name_applied_or_planned_changes_and_leave_out_zeros():
    total = SUMMARY.categories[0]

    assert category_actions(total, dry_run=False) == "1,544 labelled, 1,500 archived"
    assert category_actions(total, dry_run=True) == "1,544 to label, 1,500 to archive"


def test_an_apply_run_names_the_review_label_and_how_to_undo():
    flags, review, run = summary_lines(SUMMARY)

    assert flags == "⚑ Needs reply 3   ⏰ Today 1   ? Low confidence 7"
    assert review == "Review low-confidence mail under Sorter/_low-confidence in Gmail."
    assert run == "Run 20261019-140211 · undo with: inbox-sorter undo 20261019-140211"


def test_a_dry_run_has_nothing_to_undo():

    _, review, run = summary_lines(replace(SUMMARY, dry_run=True))

    assert review == "An apply run labels low-confidence mail Sorter/_low-confidence for review."
    assert run == "Run 20261019-140211 · dry run, nothing to undo"


def test_summary_records_as_logfmt():
    assert summary_records(SUMMARY, json_lines=False) == [
        "total category=receipts label=Receipts messages=1544 labelled=1544 archived=1500 read=0",
        "summary run=20261019-140211 mode=apply decided=1544 failed=2 reply=3 today=1 low=7 "
        "elapsed=126 review=Sorter/_low-confidence",
    ]


def test_summary_records_as_json_keep_the_key_order():
    total, summary = [json.loads(line) for line in summary_records(SUMMARY, json_lines=True)]

    assert list(total) == ["event", "category", "label", "messages", "labelled", "archived", "read"]
    assert list(summary) == [
        "event", "run", "mode", "decided", "failed", "reply", "today", "low", "elapsed", "review",
    ]  # fmt: skip
    assert summary["review"] == "Sorter/_low-confidence"


def test_print_summary_renders_without_categories():
    out = io.StringIO()


    print_summary(replace(SUMMARY, categories=(), decided=0), out)

    assert out.getvalue().startswith("Summary · APPLY · 0 decided")
