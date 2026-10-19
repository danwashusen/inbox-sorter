"""Category bars: one row per category with a name, a bar and its numbers as text.

Shared by setup's accuracy report and the run dashboard (A-category-bars).
Bars are full blocks and every row carries its numbers as text, so they read
in a monochrome terminal (R-monochrome-safe).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from rich.table import Table
from rich.text import Text

from inbox_sorter.ui.table import one_line

BAR_WIDTH = 20
BLOCK = "█"


@dataclass(frozen=True)
class Bar:
    name: str  # a label name, from Gmail or the config; never read as markup
    value: int
    scale: int  # the value of a full bar
    text: str  # the numbers shown after the bar


def bar_cells(value: int, scale: int, width: int = BAR_WIDTH) -> str:
    """``value / scale`` of ``width`` as full blocks, padded with spaces to ``width``."""
    filled = 0 if scale <= 0 else min(max(round(width * value / scale), 0), width)
    return BLOCK * filled + " " * (width - filled)


def category_bars(bars: Sequence[Bar]) -> Table:
    """A borderless grid of name, bar and text columns."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column()
    grid.add_column(no_wrap=True)
    grid.add_column()
    for bar in bars:
        cells = bar_cells(bar.value, bar.scale)
        grid.add_row(Text(one_line(bar.name)), Text(cells), Text(bar.text))
    return grid
