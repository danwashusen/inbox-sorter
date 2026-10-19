import io
import json

from inbox_sorter.setup import AccuracyReport, CategoryAccuracy, Confusion
from inbox_sorter.ui.accuracy import (
    AccuracyProgress,
    accuracy_bars,
    accuracy_heading,
    accuracy_records,
    print_accuracy,
)
from inbox_sorter.ui.bars import Bar

REPORT = AccuracyReport(
    engine="fake",
    categories=(
        CategoryAccuracy("receipts", "Receipts", 10, 8),
        CategoryAccuracy("alerts", "Sorter/My alerts", 0, 0),
    ),
    confusions=(Confusion("receipts", "alerts", 2),),
)


def test_records_come_in_order_with_their_keys():
    assert accuracy_records(REPORT, False) == [
        "category id=receipts label=Receipts sampled=10 correct=8",
        'category id=alerts label="Sorter/My alerts" sampled=0 correct=0',
        "confusion actual=receipts predicted=alerts count=2",
        "accuracy engine=fake sampled=10 correct=8",
    ]


def test_json_records_carry_the_event_name_first():
    records = [json.loads(line) for line in accuracy_records(REPORT, True)]

    assert [list(r)[0] for r in records] == ["event"] * 4
    assert records[0] == {
        "event": "category", "id": "receipts", "label": "Receipts", "sampled": 10, "correct": 8
    }
    assert records[-1] == {"event": "accuracy", "engine": "fake", "sampled": 10, "correct": 8}


def test_bars_fill_by_correct_share_with_the_numbers_as_text():
    assert accuracy_bars(REPORT) == [
        Bar("Receipts", 8, 10, "8/10  80%"),
        Bar("Sorter/My alerts", 0, 0, "no labelled mail"),
    ]


def test_heading_gives_the_total_or_says_there_was_nothing_to_check():
    assert accuracy_heading(REPORT) == "Accuracy · fake · 8/10 correct (80%)"
    empty = AccuracyReport("fake", (CategoryAccuracy("alerts", "Sorter/Alerts", 0, 0),), ())
    assert accuracy_heading(empty) == "Accuracy · fake · no labelled mail to check"


def test_plain_progress_every_ten_messages_except_after_the_last():
    out = io.StringIO()
    with AccuracyProgress(plain=True, json_lines=False, out=out) as progress:
        for done in range(1, 26):
            progress(done, 25)

    assert out.getvalue().splitlines() == ["progress done=10 total=25", "progress done=20 total=25"]


def test_terminal_progress_writes_no_plain_records():
    out = io.StringIO()
    with AccuracyProgress(plain=False, json_lines=False, out=out) as progress:
        for done in range(1, 26):
            progress(done, 25)

    assert "progress done=" not in out.getvalue()


def test_terminal_report_shows_labels_literally():
    report = AccuracyReport(
        "fake",
        (CategoryAccuracy("x", "[bold]x", 2, 1), CategoryAccuracy("y", "Y", 1, 1)),
        (Confusion("x", "y", 1),),
    )
    out = io.StringIO()

    print_accuracy(report, out)

    text = out.getvalue()
    assert "Accuracy · fake · 2/3 correct (67%)" in text
    assert "[bold]x" in text and "1/2  50%" in text
    assert "Most common confusions" in text and "[bold]x → Y  1" in text
