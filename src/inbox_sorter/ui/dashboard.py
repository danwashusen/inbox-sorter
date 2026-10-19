"""The live run dashboard: an inline Rich panel below the run table (ux.md §Run dashboard).

Each decision's table row prints into the scrollback above the panel; the
panel redraws in place and is erased when the run ends, so the summary
replaces it and the scrollback stays intact. Rich's refresh thread draws the
panel, so elapsed time and the countdowns move between messages. Every
colour is paired with text or an icon (R-monochrome-safe), and no email
body is ever shown.
"""

import logging
import math
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TextIO

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from inbox_sorter.pace import Pace
from inbox_sorter.summary import SummaryTally
from inbox_sorter.types import (
    Decision,
    Email,
    MessageDecided,
    MessageFailed,
    Retrying,
    RunEvent,
    RunFinished,
    RunStarted,
    RunStopped,
)
from inbox_sorter.ui.bars import category_bars
from inbox_sorter.ui.summary import (
    LOW_CONFIDENCE_ICON,
    NEEDS_REPLY_ICON,
    TODAY_ICON,
    format_elapsed,
    summary_bars,
    summary_lines,
)
from inbox_sorter.ui.table import TableReporter, fit, one_line

FEED_SIZE = 5  # the last few decisions
PROGRESS_WIDTH = 30
LISTING = "listing unprocessed inbox mail…"
FOOTER = "q stop after current · Ctrl-C twice to abort"
FOOTER_NO_Q = "Ctrl-C stop after current · twice to abort"  # stdin is not a foreground terminal


def _local_now() -> datetime:
    return datetime.now().astimezone()


def count_text(done: int, total: int | None) -> str:
    """Such as "6,412 / 10,233   62%"; just the count when the total is unknown."""
    if total is None:
        return f"{done:,}"
    if total <= 0:
        return f"{done:,} / {total:,}"
    return f"{done:,} / {total:,}   {done * 100 // total}%"


def pace_text(
    rate: float | None, elapsed: float, eta: float | None, finish: datetime | None
) -> str:
    """Such as "23.4 msg/min · elapsed 4h 34m · ETA 2h 43m · Sat 03:12"; ``rate`` is per second."""
    if rate is None:
        return f"– msg/min · elapsed {format_elapsed(elapsed)} · ETA –"
    text = f"{rate * 60:.1f} msg/min · elapsed {format_elapsed(elapsed)}"
    if eta is None:
        return f"{text} · ETA –"
    text = f"{text} · ETA {format_elapsed(eta)}"
    return text if finish is None else f"{text} · {finish:%a %H:%M}"


def retry_text(event: Retrying, left: float) -> str:
    """The retry status line, counting down ``left`` seconds to the next attempt.

    Once the wait is over the attempt is running, until the event that clears the line.
    """
    when = f"in {math.ceil(left)}s" if left > 0 else "now"
    return (
        f"⟳ retrying {when} "
        f"(attempt {event.attempt} of {event.attempts}): {one_line(event.error)}"
    )


def feed_text(at: datetime, email: Email, decision: Decision) -> str:
    """One feed line: time, sender, subject, category, confidence and flag icons."""
    icons = [
        icon
        for icon, flag in (
            (NEEDS_REPLY_ICON, decision.needs_reply),
            (TODAY_ICON, decision.today),
            (LOW_CONFIDENCE_ICON, decision.low_confidence),
        )
        if flag
    ]
    cells = [
        f"{at:%H:%M:%S}",
        fit(email.sender, 16),
        fit(email.subject, 28),
        fit(decision.category, 14),
        f"{round(decision.confidence * 100)}%",
        " ".join(icons),
    ]
    return "  ".join(cells).rstrip()


def failed_text(at: datetime, event: MessageFailed) -> str:
    return f"{at:%H:%M:%S}  ✗ {one_line(event.message_id)} failed: {one_line(event.error)}"


def title_text(started: RunStarted | None, device: str | None) -> Text:
    """Such as "inbox-sorter · APPLY · clef-local on MPS"; the mode is a word, not just a colour."""
    title = Text("inbox-sorter")
    if started is None:
        return title
    title.append(" · ")
    if started.dry_run:
        title.append("DRY RUN", style="bold green")
    else:
        title.append("APPLY", style="bold red")
    title.append(f" · {one_line(started.engine)}")
    if device:
        title.append(f" on {one_line(device).upper()}")
    return title


class Dashboard:
    """A context manager showing the live panel, and a reporter over RunEvents.

    Feed ``tally`` each event before the dashboard: the panel's categories,
    flags and elapsed time come from it. The dashboard's own state sits
    behind one lock; nothing calls into Rich while holding it, since the
    refresh thread takes Rich's lock and then this one. The tally is read
    unlocked: its summary() only indexes counts, so a frame may be one event
    out of step with the feed but never fails.
    """

    def __init__(
        self,
        tally: SummaryTally,
        out: TextIO,
        device: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        self._tally = tally
        self._device = device
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._pace = Pace(clock)
        self._started: RunStarted | None = None
        self._feed: deque[str] = deque(maxlen=FEED_SIZE)
        self._retry: tuple[Retrying, float] | None = None  # the event and when its wait ends
        self._table = TableReporter(out)  # only for its lines; never writes
        self._console = Console(file=out, highlight=False)
        self._live: Live | None = None
        self._streams: tuple[tuple[object, ...], tuple[object, ...]] | None = None
        # Set from the SIGINT handler and the q thread: a bare attribute,
        # no lock or I/O (see stop.StopFlag).
        self.stopping: str | None = None
        self.q_key = True  # False when stop.listen_for_stop has no `q` listener

    def __enter__(self) -> "Dashboard":
        streams = sys.stdout, sys.stderr
        self._live = Live(
            get_renderable=self.panel,
            console=self._console,
            transient=True,
            refresh_per_second=4,
        )
        self._live.start()  # in a terminal, swaps sys.stdout and sys.stderr for proxies
        proxies = sys.stdout, sys.stderr
        try:
            _reroute_logging(streams, proxies)
        except BaseException:
            self.__exit__()
            raise
        self._streams = streams, proxies
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            if self._streams is not None:
                streams, proxies = self._streams
                self._streams = None
                # Before the proxies go; this also catches handlers made during the run.
                _reroute_logging(proxies, streams)
        finally:
            if self._live is not None:
                self._live.stop()  # transient: erases the panel, keeps the rows above it
                self._live = None

    def notice(self, text: str) -> None:
        """Show ``text`` in the footer; safe in a signal handler."""
        self.stopping = text

    def __call__(self, event: RunEvent) -> None:
        at = self._now()
        with self._lock:
            self._pace(event)
            match event:
                case RunStarted():
                    self._started = event
                    self._retry = None
                case MessageDecided(email=email, decision=decision):
                    self._feed.appendleft(feed_text(at, email, decision))
                    self._retry = None
                case MessageFailed():
                    self._feed.appendleft(failed_text(at, event))
                    self._retry = None
                case Retrying():
                    self._retry = (event, self._clock() + event.delay)
                case RunFinished() | RunStopped():
                    self._retry = None
        for line in self._table.lines(event):
            # A Text, never markup; soft_wrap leaves a long row to the terminal.
            self._console.print(Text(line), soft_wrap=True)
        if self._live is not None:
            self._live.refresh()  # printing redraws the old panel; draw this event now

    def panel(self) -> Panel:
        """The panel as it stands now; called on Rich's refresh thread."""
        with self._lock:
            started = self._started
            done, total = self._pace.done, self._pace.total
            rate, eta = self._pace.rate(), self._pace.eta()
            feed = list(self._feed)
            retry = self._retry
        # The tally is fed first, so it has started whenever the dashboard has.
        summary = self._tally.summary() if started is not None and self._tally.started else None
        parts: list[RenderableType] = []
        if summary is None:
            parts.append(Text(LISTING))
        else:
            finish = None if eta is None else self._now() + timedelta(seconds=eta)
            progress = Table.grid(padding=(0, 2))
            progress.add_row(
                ProgressBar(
                    total=None if total is None else max(total, 1),  # None pulses
                    completed=done,
                    width=PROGRESS_WIDTH,
                ),
                Text(count_text(done, total)),
            )
            parts += [progress, Text(pace_text(rate, summary.elapsed, eta, finish))]
        if retry is not None:
            event, until = retry
            parts.append(_cropped(retry_text(event, until - self._clock())))
        if summary is not None:
            parts.append(Rule(style="dim"))
            if summary.categories:
                parts.append(category_bars(summary_bars(summary)))
            parts.append(Text(summary_lines(summary)[0]))  # counts only, no email text
            if feed:
                parts += [Rule(style="dim"), _cropped("\n".join(feed))]
        return Panel(
            Group(*parts),
            title=title_text(started, self._device),
            title_align="left",
            subtitle=Text(self.stopping or (FOOTER if self.q_key else FOOTER_NO_Q)),
            subtitle_align="left",
        )


def _reroute_logging(old: tuple[object, ...], new: tuple[object, ...]) -> None:
    """Point logging handlers that hold a stream in ``old`` at its match in ``new``.

    A library's handler keeps the stderr it was made with, so a warning
    during the run (transformers logs some on clef's first decision) would
    write past Rich's proxy and break the panel's redraw. Rerouted, it prints
    above the panel. Handlers that look up sys.stderr on each write, such as
    logging's last resort, already follow the proxy.
    """
    if old == new:  # not a terminal: Live installed no proxies
        return
    loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
    for logger in loggers:
        for handler in getattr(logger, "handlers", ()):  # a PlaceHolder has none
            # The instance's own stream, not a property that looks it up.
            stream = vars(handler).get("stream")
            if isinstance(handler, logging.StreamHandler) and stream in old:
                handler.setStream(new[old.index(stream)])


def _cropped(text: str) -> Text:
    """Lines cut with "…" at the panel's edge rather than wrapped; the full row is above."""
    return Text(text, no_wrap=True, overflow="ellipsis")
