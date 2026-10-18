import io

from inbox_sorter.triage import decide
from inbox_sorter.types import MessageDecided, MessageFailed, RunFinished, RunStarted
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
