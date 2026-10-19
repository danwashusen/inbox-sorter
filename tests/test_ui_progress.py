import io

from inbox_sorter.ui.progress import CountProgress


def test_plain_progress_every_ten_items_except_after_the_last():
    out = io.StringIO()
    with CountProgress("undoing run r1", plain=True, json_lines=False, out=out) as progress:
        for done in range(1, 26):
            progress(done, 25)

    assert out.getvalue().splitlines() == ["progress done=10 total=25", "progress done=20 total=25"]


def test_terminal_progress_writes_no_plain_records():
    out = io.StringIO()
    with CountProgress("undoing run r1", plain=False, json_lines=False, out=out) as progress:
        for done in range(1, 26):
            progress(done, 25)

    assert "progress done=" not in out.getvalue()
