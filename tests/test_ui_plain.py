import io
import json
import shlex

from inbox_sorter.triage import decide
from inbox_sorter.types import (
    MessageDecided,
    MessageFailed,
    Retrying,
    RunFinished,
    RunStarted,
    RunStopped,
)
from inbox_sorter.ui.plain import PlainReporter, plain_line, progress_line
from tests.factories import make_config, make_email, response

STARTED = RunStarted("20261018-140211", dry_run=True, engine="clef-local", total=2)
EMAIL = make_email(sender="Shop <orders@shop.example>", subject="Order 42")
DECIDED = MessageDecided(EMAIL, decide(EMAIL, response(reply=0.9), make_config(), False))
FAILED = MessageFailed("m2", "no message 'm2'")
FINISHED = RunFinished("20261018-140211", decided=1, failed=1)


def keys(line):
    return [field.split("=", 1)[0] for field in shlex.split(line)[1:]]


def test_records_have_their_keys_in_order():
    assert plain_line(STARTED, False) == (
        "started run=20261018-140211 mode=dry-run engine=clef-local total=2"
    )
    assert plain_line(DECIDED, False) == (
        'decided id=m1 category=receipts confidence=0.97 reply=true today=false low=false '
        'body=false add="Receipts,Sorter/Needs reply,Sorter/_processed" remove="" '
        'sender="Shop <orders@shop.example>" subject="Order 42"'
    )
    assert plain_line(FAILED, False) == "failed id=m2 error=\"no message 'm2'\""
    assert plain_line(FINISHED, False) == "finished run=20261018-140211 decided=1 failed=1"
    assert progress_line(10, 25, False) == "progress done=10 total=25"


def test_logfmt_escapes_quotes_backslashes_and_newlines():
    email = make_email(subject='a "b" c=d \\ e\nf')
    line = plain_line(MessageDecided(email, DECIDED.decision), False)

    assert line.endswith('subject="a \\"b\\" c=d \\\\ e\\nf"')
    assert "\n" not in line


def test_logfmt_quotes_and_escapes_other_control_characters():
    email = make_email(subject="a\rb\x07c\u2028d")
    line = plain_line(MessageDecided(email, DECIDED.decision), False)

    assert line.endswith('subject="a\\rb\\u0007c\\u2028d"')


def test_json_lines_carry_the_same_keys():
    for event in (STARTED, DECIDED, FAILED, FINISHED):
        record = json.loads(plain_line(event, True))
        assert list(record)[0] == "event"
        assert list(record)[1:] == keys(plain_line(event, False))

    record = json.loads(plain_line(DECIDED, True))
    assert record["event"] == "decided"
    assert record["confidence"] == 0.97 and record["reply"] is True
    assert record["add"] == ["Receipts", "Sorter/Needs reply", "Sorter/_processed"]
    assert json.loads(progress_line(10, 25, True)) == {"event": "progress", "done": 10, "total": 25}


def test_json_keeps_non_ascii_readable():
    email = make_email(subject="Café")

    assert '"subject": "Café"' in plain_line(MessageDecided(email, DECIDED.decision), True)


def progress_records(total):
    out = io.StringIO()
    report = PlainReporter(out)
    report(RunStarted("r", dry_run=True, engine="fake", total=total))
    for _ in range(total):
        report(DECIDED)
    report(RunFinished("r", decided=total, failed=0))
    return [line for line in out.getvalue().splitlines() if line.startswith("progress")]


def test_progress_every_ten_messages_except_after_the_last():
    assert progress_records(25) == ["progress done=10 total=25", "progress done=20 total=25"]
    assert progress_records(20) == ["progress done=10 total=20"]


def test_no_body_is_printed():
    email = make_email(body="SECRET BODY TEXT")
    out = io.StringIO()

    PlainReporter(out)(MessageDecided(email, DECIDED.decision))
    PlainReporter(out, json_lines=True)(MessageDecided(email, DECIDED.decision))

    assert "SECRET" not in out.getvalue()


def test_progress_with_an_unknown_total_leaves_total_empty():
    out = io.StringIO()
    report = PlainReporter(out)
    report(RunStarted("r", dry_run=True, engine="fake", total=None))
    for _ in range(10):
        report(DECIDED)

    assert out.getvalue().splitlines()[-1] == 'progress done=10 total=""'


def test_json_lines_escape_line_separators_and_bidi_controls():
    email = make_email(subject="a b\x85c‮d")
    line = plain_line(MessageDecided(email, DECIDED.decision), True)

    assert len(line.splitlines()) == 1
    assert "‮" not in line
    assert json.loads(line)["subject"] == "a b\x85c‮d"


def test_logfmt_escapes_bidi_controls():
    email = make_email(subject="pay‮lap")

    assert plain_line(MessageDecided(email, DECIDED.decision), False).endswith(
        'subject="pay\\u202elap"'
    )


RETRYING = Retrying("m3", attempt=2, attempts=10, delay=4.0, error="Gmail request failed (429): x")
LISTING_RETRY = Retrying(None, attempt=1, attempts=10, delay=1.0, error="could not reach Gmail")
STOPPED = RunStopped("20261018-140211", decided=1, failed=0, remaining=3)


def test_retrying_and_stopped_records_as_logfmt():
    assert plain_line(RETRYING, False) == (
        'retrying id=m3 attempt=2 of=10 delay=4.00 error="Gmail request failed (429): x"'
    )
    assert plain_line(LISTING_RETRY, False) == (
        'retrying id="" attempt=1 of=10 delay=1.00 error="could not reach Gmail"'
    )
    assert plain_line(STOPPED, False) == (
        "stopped run=20261018-140211 decided=1 failed=0 remaining=3"
    )


def test_retrying_and_stopped_records_as_json_keep_the_key_order():
    retrying = json.loads(plain_line(RETRYING, True))
    listing = json.loads(plain_line(LISTING_RETRY, True))
    stopped = json.loads(plain_line(STOPPED, True))

    assert list(retrying) == ["event", "id", "attempt", "of", "delay", "error"]
    assert (retrying["event"], retrying["delay"]) == ("retrying", 4.0)
    assert listing["id"] is None
    assert list(stopped) == ["event", "run", "decided", "failed", "remaining"]
    assert stopped["remaining"] == 3


def test_a_retry_is_not_counted_as_a_message():
    out = io.StringIO()
    report = PlainReporter(out, progress_every=1)

    report(RunStarted("r1", dry_run=True, engine="fake", total=5))
    report(RETRYING)

    assert [line.split()[0] for line in out.getvalue().splitlines()] == ["started", "retrying"]
