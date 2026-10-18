import io

import pytest

from inbox_sorter.gmail import LabelInfo
from inbox_sorter.setup import SetupCancelled
from inbox_sorter.ui.prompts import LinePrompter, preview_lines
from tests.factories import make_email

CHOICES = ["label", "label, mark read", "label, archive"]


def prompter(text: str) -> tuple[LinePrompter, io.StringIO]:
    out = io.StringIO()
    return LinePrompter(io.StringIO(text), out), out


def test_blank_lines_take_the_default():
    line, _ = prompter("\n\n\n\n")

    assert line.confirm("Use it?", True) is True
    assert line.confirm("Add one?", False) is False
    assert line.text("Description", "Bills") == "Bills"
    assert line.select("Actions", CHOICES, "label") == "label"


@pytest.mark.parametrize("answer, expected", [("y", True), ("YES", True), ("N", False), ("no", False)])
def test_confirm_parses_yes_and_no(answer, expected):
    line, _ = prompter(answer + "\n")

    assert line.confirm("Use it?", not expected) is expected


def test_confirm_shows_the_default_and_reasks_invalid_input():
    line, out = prompter("maybe\ny\n")

    assert line.confirm("Use Bills as a category?", False) is True
    assert out.getvalue() == (
        "Use Bills as a category? [y/N]: answer y or n\nUse Bills as a category? [y/N]: "
    )


def test_text_returns_the_typed_answer():
    line, out = prompter("Bills to pay\n")

    assert line.text("Description for Bills", "Bills") == "Bills to pay"
    assert out.getvalue() == "Description for Bills [Bills]: "


def test_select_takes_a_number_and_reasks_one_out_of_range():
    line, out = prompter("7\nx\n3\n")

    assert line.select("Actions", CHOICES, "label") == "label, archive"
    assert out.getvalue().splitlines()[:4] == [
        "  1) label",
        "  2) label, mark read",
        "  3) label, archive",
        "Actions [1]: enter a number from 1 to 3",
    ]


def test_end_of_input_cancels():
    line, _ = prompter("y\n")
    line.confirm("Use it?", True)

    with pytest.raises(SetupCancelled, match="input ended before setup finished"):
        line.text("Description", "Bills")


def test_ctrl_c_cancels():
    class Interrupted(io.StringIO):
        def readline(self, *args):
            raise KeyboardInterrupt

    line = LinePrompter(Interrupted(), io.StringIO())

    with pytest.raises(SetupCancelled, match="setup cancelled"):
        line.confirm("Use it?", True)


def test_preview_lines_fold_and_mask_untrusted_headers():
    bills = LabelInfo(id="L1", name="Bills", system=False, message_count=42)
    samples = [
        make_email("m1", sender="Power Co <bills@power.example>", subject="Your\n  bill"),
        make_email("m2", sender="Evil\x1b[2J", subject="Pay ‮exe.fdp"),
    ]

    lines = preview_lines(bills, samples)

    assert lines[0] == "Bills · 42 messages"
    assert lines[1] == "  Power Co <bills@pow…  Your bill"
    assert lines[2].startswith("  Evil?[2J")
    assert "Pay ?exe.fdp" in lines[2]
    assert len(lines) == 3


def test_preview_lines_without_samples_is_the_header_only():
    empty = LabelInfo(id="L1", name="Empty", system=False, message_count=0)

    assert preview_lines(empty, []) == ["Empty · 0 messages"]


def test_input_that_is_not_utf8_cancels():
    inp = io.TextIOWrapper(io.BytesIO(b"\xff\n"), encoding="utf-8")
    line = LinePrompter(inp, io.StringIO())

    with pytest.raises(SetupCancelled, match="not UTF-8"):
        line.confirm("Use it?", True)
