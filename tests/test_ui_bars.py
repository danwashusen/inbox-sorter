import pytest

from inbox_sorter.ui.bars import BAR_WIDTH, bar_cells


@pytest.mark.parametrize(
    "value, scale, blocks",
    [(0, 10, 0), (5, 10, 10), (10, 10, 20), (12, 10, 20), (3, 0, 0)],
)
def test_bar_cells_fill_the_share_and_pad_to_the_width(value, scale, blocks):
    cells = bar_cells(value, scale)

    assert BAR_WIDTH == 20
    assert cells == "█" * blocks + " " * (20 - blocks)
