"""The end-of-run summary: category bars on a terminal, records in plain mode (ux.md §Summary).

Every number is paired with text, so it reads in a monochrome terminal
(R-monochrome-safe). No email content is printed.
"""

from typing import TextIO

from rich.console import Console
from rich.text import Text

from inbox_sorter.summary import CategoryTotal, RunSummary
from inbox_sorter.types import LOW_CONFIDENCE_LABEL
from inbox_sorter.ui.bars import Bar, category_bars
from inbox_sorter.ui.plain import record
from inbox_sorter.ui.table import one_line

# Action words per mode; a dry run applied nothing.
APPLIED = ("labelled", "archived", "read")
PLANNED = ("to label", "to archive", "to mark read")


def format_elapsed(seconds: float) -> str:
    """Such as "59s", "1m 01s" or "4h 34m"."""
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m {whole % 60:02d}s"
    return f"{whole // 3600}h {whole % 3600 // 60:02d}m"


def summary_heading(summary: RunSummary) -> str:
    mode = "DRY RUN" if summary.dry_run else "APPLY"
    return (
        f"Summary · {mode} · {summary.decided:,} decided · {summary.failed:,} failed · "
        f"elapsed {format_elapsed(summary.elapsed)}"
    )


def category_actions(total: CategoryTotal, dry_run: bool) -> str:
    """Such as "1,544 labelled, 1,500 archived"; a zero count is left out."""
    words = PLANNED if dry_run else APPLIED
    counts = (total.labelled, total.archived, total.marked_read)
    pairs = zip(counts, words, strict=True)
    return ", ".join(f"{count:,} {word}" for count, word in pairs if count)


def summary_bars(summary: RunSummary) -> list[Bar]:
    """One bar per category, filled by its share of the largest category."""
    scale = max((total.messages for total in summary.categories), default=0)
    return [
        Bar(
            name=total.category,
            value=total.messages,
            scale=scale,
            text=f"{total.messages:,}  {category_actions(total, summary.dry_run)}".rstrip(),
        )
        for total in summary.categories
    ]


def summary_lines(summary: RunSummary) -> tuple[str, str, str]:
    """The flag counts, where to review, and the run ID with how to undo it."""
    flags = (
        f"⚑ Needs reply {summary.needs_reply:,}   ⏰ Today {summary.today:,}   "
        f"? Low confidence {summary.low_confidence:,}"
    )
    if summary.dry_run:
        review = f"An apply run labels low-confidence mail {LOW_CONFIDENCE_LABEL} for review."
        run = f"Run {summary.run_id} · dry run, nothing to undo"
    else:
        review = f"Review low-confidence mail under {LOW_CONFIDENCE_LABEL} in Gmail."
        run = f"Run {summary.run_id} · undo with: inbox-sorter undo {summary.run_id}"
    return flags, review, run


def print_summary(summary: RunSummary, out: TextIO) -> None:
    """The heading, the category bars, then the flag, review and run lines."""
    console = Console(file=out, highlight=False)
    console.print(Text(summary_heading(summary)))
    if summary.categories:
        console.print(category_bars(summary_bars(summary)))
    for line in summary_lines(summary):
        console.print(Text(one_line(line)))


def notification_text(summary: RunSummary | None, error: str | None) -> str:
    """The macOS notification for a finished run or a fatal error (R-notify).

    ``summary`` is None when the run ended before it started. Counts only:
    no email content leaves the terminal (C-local-only).
    """
    if error is not None:
        if summary is None:
            return f"Run stopped on an error: {error}"
        return f"Run {summary.run_id} stopped on an error: {error}"
    if summary is None:
        raise ValueError("a finished run needs its summary")
    mode = "Dry run" if summary.dry_run else "Run"
    return (
        f"{mode} {summary.run_id} finished: {summary.decided:,} decided, "
        f"{summary.failed:,} failed"
    )


def summary_records(summary: RunSummary, json_lines: bool) -> list[str]:
    """One total record per category, then the summary record."""
    lines = [
        record(
            "total",
            {
                "category": total.category,
                "label": total.gmail_label,
                "messages": total.messages,
                "labelled": total.labelled,
                "archived": total.archived,
                "read": total.marked_read,
            },
            json_lines,
        )
        for total in summary.categories
    ]
    lines.append(
        record(
            "summary",
            {
                "run": summary.run_id,
                "mode": "dry-run" if summary.dry_run else "apply",
                "decided": summary.decided,
                "failed": summary.failed,
                "reply": summary.needs_reply,
                "today": summary.today,
                "low": summary.low_confidence,
                "elapsed": int(summary.elapsed),  # whole seconds, as format_elapsed shows
                "review": LOW_CONFIDENCE_LABEL,
            },
            json_lines,
        )
    )
    return lines
