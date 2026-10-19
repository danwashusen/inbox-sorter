"""Setup's accuracy report: category bars on a terminal, records in plain mode.

No email content is ever printed: only label names, category IDs, counts and
the engine name.
"""

from typing import TextIO

from rich.console import Console
from rich.text import Text

from inbox_sorter.setup import AccuracyReport
from inbox_sorter.ui.bars import Bar, category_bars
from inbox_sorter.ui.plain import record
from inbox_sorter.ui.table import one_line

NO_LABELLED_MAIL = "no labelled mail to check accuracy against"
CONFIG_KEPT = "the config on disk is as reported above"
ACCURACY_STOPPED = f"accuracy check stopped; {CONFIG_KEPT}"


def _percent(correct: int, sampled: int) -> int:
    return round(100 * correct / sampled)


def accuracy_heading(report: AccuracyReport) -> str:
    """Such as "Accuracy · clef-local · 8/10 correct (80%)"."""
    if report.sampled == 0:
        return f"Accuracy · {report.engine} · no labelled mail to check"
    return (
        f"Accuracy · {report.engine} · {report.correct}/{report.sampled} correct "
        f"({_percent(report.correct, report.sampled)}%)"
    )


def accuracy_bars(report: AccuracyReport) -> list[Bar]:
    """One bar per enabled category, filled by its share of correct guesses."""
    return [
        Bar(
            name=category.gmail_label,
            value=category.correct,
            scale=category.sampled,
            text=(
                f"{category.correct}/{category.sampled}  "
                f"{_percent(category.correct, category.sampled)}%"
                if category.sampled
                else "no labelled mail"
            ),
        )
        for category in report.categories
    ]


def accuracy_records(report: AccuracyReport, json_lines: bool) -> list[str]:
    """The category records, then the confusions, then the accuracy total."""
    lines = [
        record(
            "category",
            {
                "id": category.category,
                "label": category.gmail_label,
                "sampled": category.sampled,
                "correct": category.correct,
            },
            json_lines,
        )
        for category in report.categories
    ]
    lines += [
        record(
            "confusion",
            {
                "actual": confusion.actual,
                "predicted": confusion.predicted,
                "count": confusion.count,
            },
            json_lines,
        )
        for confusion in report.confusions
    ]
    lines.append(
        record(
            "accuracy",
            {"engine": report.engine, "sampled": report.sampled, "correct": report.correct},
            json_lines,
        )
    )
    return lines


def print_accuracy(report: AccuracyReport, out: TextIO) -> None:
    """The heading, the category bars, then the most common confusions."""
    console = Console(file=out, highlight=False)
    console.print(Text(one_line(accuracy_heading(report))))
    console.print(category_bars(accuracy_bars(report)))
    if report.confusions:
        labels = {category.category: category.gmail_label for category in report.categories}
        console.print(Text("Most common confusions"))
        for confusion in report.confusions:
            actual = one_line(labels[confusion.actual])
            predicted = one_line(labels[confusion.predicted])
            console.print(Text(f"{actual} → {predicted}  {confusion.count}"))

