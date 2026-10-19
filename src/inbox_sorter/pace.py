"""The run's rate and ETA: a moving average over the most recent messages (ux.md §Long runs).

Pure, with no Rich: ui/dashboard.py renders it. On a resumed run,
RunStarted.total counts only the remaining mail, so the ETA does too.
"""

import time
from collections import deque
from collections.abc import Callable

from inbox_sorter.types import MessageDecided, MessageFailed, RunEvent, RunStarted

# Messages in the rate window: about 3.6 min at 10.8 s each, so the rate
# settles after a few minutes and follows slowdowns.
RATE_WINDOW = 20


class Pace:
    """A reporter that times each finished message; ``rate()`` and ``eta()`` read it.

    A decided or failed message counts as done. The window starts at this
    run's RunStarted.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, window: int = RATE_WINDOW) -> None:
        self._clock = clock
        self._window = window
        self._total: int | None = None
        self._done = 0
        self._times: deque[float] = deque(maxlen=window + 1)

    @property
    def total(self) -> int | None:
        return self._total

    @property
    def done(self) -> int:
        return self._done

    def __call__(self, event: RunEvent) -> None:
        match event:
            case RunStarted():
                self._total = event.total
                self._done = 0
                self._times = deque([self._clock()], maxlen=self._window + 1)
            case MessageDecided() | MessageFailed():
                self._done += 1
                self._times.append(self._clock())

    def rate(self) -> float | None:
        """Messages per second over the window; None before the first message."""
        if len(self._times) < 2:
            return None
        span = self._times[-1] - self._times[0]
        if span <= 0:
            return None
        return (len(self._times) - 1) / span

    def eta(self) -> float | None:
        """Seconds left, counting down from the last finished message; None if unknown."""
        if self._total is None:
            return None
        remaining = self._total - self._done
        if remaining <= 0:
            return 0.0
        rate = self.rate()
        if rate is None:
            return None
        return max(self._times[-1] + remaining / rate - self._clock(), 0.0)
