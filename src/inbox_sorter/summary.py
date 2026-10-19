"""The end-of-run summary: tallied from RunEvents, rendered by ui/summary.py (ux.md §Summary).

The run dashboard and the stop path consume it too, so it imports no UI.
"""

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from inbox_sorter.config import Config
from inbox_sorter.types import MessageDecided, MessageFailed, RunEvent, RunFinished, RunStarted


@dataclass(frozen=True)
class CategoryTotal:
    category: str  # the category ID
    gmail_label: str
    messages: int
    labelled: int  # the category label is among the labels to add
    archived: int  # INBOX is among the labels to remove
    marked_read: int  # UNREAD is among the labels to remove


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    dry_run: bool
    engine: str
    categories: tuple[CategoryTotal, ...]  # those with a message, in config order
    decided: int
    failed: int
    needs_reply: int
    today: int
    low_confidence: int
    elapsed: float  # seconds


class SummaryTally:
    """A reporter that counts each RunEvent; ``summary()`` gives the totals so far.

    Elapsed time runs from RunStarted to RunFinished, or to now on ``clock``
    when the run has not finished.
    """

    def __init__(self, config: Config, clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        self._clock = clock
        self._run: RunStarted | None = None
        self._start = 0.0
        self._end: float | None = None
        self._counts: dict[str, Counter[str]] = {c.id: Counter() for c in config.categories}
        self._flags: Counter[str] = Counter()

    @property
    def started(self) -> bool:
        """True once RunStarted has been seen, so ``summary()`` can be called."""
        return self._run is not None

    def __call__(self, event: RunEvent) -> None:
        match event:
            case RunStarted():
                self._run = event
                self._start = self._clock()
            case MessageDecided(decision=decision):
                category = next(c for c in self._config.categories if c.id == decision.category)
                counts = self._counts[category.id]
                counts["messages"] += 1
                counts["labelled"] += category.gmail_label in decision.add_labels
                counts["archived"] += "INBOX" in decision.remove_labels
                counts["marked_read"] += "UNREAD" in decision.remove_labels
                self._flags["decided"] += 1
                self._flags["needs_reply"] += decision.needs_reply
                self._flags["today"] += decision.today
                self._flags["low_confidence"] += decision.low_confidence
            case MessageFailed():
                self._flags["failed"] += 1
            case RunFinished():
                self._end = self._clock()

    def summary(self) -> RunSummary:
        if self._run is None:
            raise ValueError("no run has started")
        end = self._end if self._end is not None else self._clock()
        categories = tuple(
            CategoryTotal(
                category=category.id,
                gmail_label=category.gmail_label,
                messages=counts["messages"],
                labelled=counts["labelled"],
                archived=counts["archived"],
                marked_read=counts["marked_read"],
            )
            for category in self._config.categories
            if (counts := self._counts[category.id])["messages"]
        )
        return RunSummary(
            run_id=self._run.run_id,
            dry_run=self._run.dry_run,
            engine=self._run.engine,
            categories=categories,
            decided=self._flags["decided"],
            failed=self._flags["failed"],
            needs_reply=self._flags["needs_reply"],
            today=self._flags["today"],
            low_confidence=self._flags["low_confidence"],
            elapsed=end - self._start,
        )
