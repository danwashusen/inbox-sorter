"""A count progress bar for long Gmail loops, such as setup's check and undo."""

from collections.abc import Callable
from typing import TextIO

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

from inbox_sorter.ui.plain import PROGRESS_EVERY, progress_line


class CountProgress:
    """A context manager giving a ``(done, total)`` progress callback.

    On a terminal it is a transient Rich bar titled ``label``; in plain mode
    it writes a progress record every 10th item except the last, as ``run``
    does.
    """

    def __init__(self, label: str, plain: bool, json_lines: bool, out: TextIO) -> None:
        self.label = label
        self.plain = plain
        self.json_lines = json_lines
        self.out = out
        self._bar: Progress | None = None

    def __enter__(self) -> Callable[[int, int], None]:
        if self.plain:
            return self._write
        self._bar = Progress(
            TextColumn(self.label, markup=False),
            BarColumn(),
            MofNCompleteColumn(),
            console=Console(file=self.out, highlight=False),
            transient=True,
        )
        self._bar.__enter__()
        task = self._bar.add_task("", total=None)
        bar = self._bar
        return lambda done, total: bar.update(task, completed=done, total=total)

    def __exit__(self, *exc: object) -> None:
        if self._bar is not None:
            self._bar.__exit__(None, None, None)

    def _write(self, done: int, total: int) -> None:
        # The last item is followed by the report, not progress.
        if done % PROGRESS_EVERY == 0 and done != total:
            print(progress_line(done, total, self.json_lines), file=self.out, flush=True)
