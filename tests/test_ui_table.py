import io

from inbox_sorter.triage import decide
from inbox_sorter.types import (
    MessageDecided,
    MessageFailed,
    Retrying,
    RunFinished,
    RunStarted,
    RunStopped,
)
from inbox_sorter.ui.table import TableReporter, planned_actions
from tests.factories import make_config, make_email, response

CONFIG = make_config()
EMAIL = make_email()


def test_planned_actions_for_an_archived_receipt():
    decision = decide(EMAIL, response(), CONFIG, False)

    assert planned_actions(decision) == "+Receipts, archive, mark read"


def test_planned_actions_for_a_flagged_message():
    decision = decide(EMAIL, response(reply=0.9), CONFIG, False)

    assert planned_actions(decision) == "+Receipts, +Sorter/Needs reply"


def test_planned_actions_for_low_confidence():
    decision = decide(EMAIL, response(confidence=0.5), CONFIG, False)

    assert planned_actions(decision) == "+Receipts, +Sorter/_low-confidence"


def test_table_streams_header_rows_and_finish_line():
    out = io.StringIO()
    report = TableReporter(out)
    decision = decide(EMAIL, response(), CONFIG, False)

    report(RunStarted("20261018-140211", dry_run=True, engine="clef-local", total=2))
    report(MessageDecided(make_email(body="SECRET BODY TEXT"), decision))
    report(MessageFailed("m2", "no message 'm2'"))
    report(RunFinished("20261018-140211", decided=1, failed=1))

    lines = out.getvalue().splitlines()
    assert lines[0] == "DRY RUN · clef-local · 2 messages · run 20261018-140211"
    assert lines[1].split() == ["Sender", "Subject", "Category", "Conf", "Planned", "actions"]
    assert "receipts" in lines[2] and "97%" in lines[2]
    assert lines[2].endswith("+Receipts, archive, mark read")
    assert lines[3] == "m2  failed: no message 'm2'"
    assert lines[4] == "Dry run finished: 1 planned, 1 failed. Gmail was not changed."
    assert "SECRET" not in out.getvalue()


def test_an_apply_run_names_the_mode_and_what_changed():
    out = io.StringIO()
    report = TableReporter(out)

    report(RunStarted("20261019-140211", dry_run=False, engine="clef-local", total=1))
    report(RunFinished("20261019-140211", decided=1, failed=0))

    lines = out.getvalue().splitlines()
    assert lines[0] == "APPLY · clef-local · 1 messages · run 20261019-140211"
    assert lines[1].split() == ["Sender", "Subject", "Category", "Conf", "Actions"]
    assert lines[2] == "Apply finished: 1 changed, 0 failed."


def test_long_cells_are_cut_with_an_ellipsis():
    out = io.StringIO()
    email = make_email(subject="x" * 50)

    TableReporter(out)(MessageDecided(email, decide(email, response(), CONFIG, False)))

    assert "x" * 35 + "…  " in out.getvalue()


def test_control_characters_in_headers_and_errors_are_not_printed_raw():
    out = io.StringIO()
    email = make_email(sender="Evil\x1b[2J <x@example>", subject="Hi\x07")
    report = TableReporter(out)

    report(MessageDecided(email, decide(email, response(), CONFIG, False)))
    report(MessageFailed("m2", "line one\nline\x1b two"))

    text = out.getvalue()
    assert "\x1b" not in text and "\x07" not in text
    assert "Evil?[2J <x@example>" in text
    assert text.splitlines()[-1] == "m2  failed: line one line? two"


def test_bidi_controls_in_headers_are_not_printed_raw():
    out = io.StringIO()
    email = make_email(sender="paypal‮moc.liame")

    TableReporter(out)(MessageDecided(email, decide(email, response(), CONFIG, False)))

    assert "‮" not in out.getvalue()
    assert "paypal?moc.liame" in out.getvalue()


def test_wide_characters_are_measured_in_terminal_cells():
    out = io.StringIO()
    email = make_email(sender="山田太郎", subject="会議" * 20)

    TableReporter(out)(MessageDecided(email, decide(email, response(), CONFIG, False)))

    row = out.getvalue().splitlines()[0]
    # 8 cells of sender pad to 20; 34 cells of subject plus "…" pad to 36.
    assert row.startswith("山田太郎" + " " * 12 + "  " + "会議" * 8 + "会… " + "  receipts")


def test_a_retry_shows_the_wait_attempt_and_error_on_one_line():
    out = io.StringIO()

    TableReporter(out)(Retrying("m1", 3, 10, 4.0, "Gmail request\nfailed (503)"))

    assert out.getvalue() == "retrying in 4s (attempt 3 of 10): Gmail request failed (503)\n"


def test_a_stopped_run_says_what_is_left_in_each_mode():
    out = io.StringIO()
    report = TableReporter(out)

    report(RunStarted("r1", dry_run=True, engine="clef-local", total=5))
    report(RunStopped("r1", decided=2, failed=1, remaining=2))
    report(RunStarted("r2", dry_run=False, engine="clef-local", total=5))
    report(RunStopped("r2", decided=2, failed=0, remaining=3))

    lines = out.getvalue().splitlines()
    assert lines[2] == "Dry run stopped: 2 planned, 1 failed, 2 not decided. Gmail was not changed."
    assert lines[5] == (
        "Stopped: 2 changed, 0 failed, 3 left. Run the same command again to resume."
    )


def test_lines_returns_the_header_and_writes_nothing():
    out = io.StringIO()
    report = TableReporter(out)

    lines = report.lines(RunStarted("r1", dry_run=False, engine="clef-local", total=3))

    assert lines[0] == "APPLY · clef-local · 3 messages · run r1"
    assert lines[1].split() == ["Sender", "Subject", "Category", "Conf", "Actions"]
    assert len(lines) == 2
    assert out.getvalue() == ""
    assert report.dry_run is False
