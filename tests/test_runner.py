import json
from datetime import UTC, datetime

import pytest

from inbox_sorter.engines.fake import FakeEngine
from inbox_sorter.errors import UserError
from inbox_sorter.gmail import PROCESSED_LABEL, LabelInfo, MailboxError
from inbox_sorter.questions import build_questions
from inbox_sorter.runlog import RunLog, RunLogError
from inbox_sorter.runner import decide_message, new_run_id, run
from inbox_sorter.types import MessageDecided, MessageFailed, RunFinished, RunStarted
from tests.factories import answers, make_config, make_email
from tests.fake_mailbox import FakeMailbox

PROCESSED = LabelInfo(id="Label_9", name=PROCESSED_LABEL, system=False, message_count=0)
RECEIPTS = LabelInfo(id="Label_5", name="Receipts", system=False, message_count=0)


def confident(request):
    return answers()


def unsure_then_sure(request):
    return answers(confidence=0.97 if "body" in request.state else 0.5, reply=0.9)


def start(mailbox, engine=None, config=None, limit=None, log=None, run_id="r1"):
    config = config or make_config()
    return run(
        mailbox,
        engine or FakeEngine(confident),
        config,
        build_questions(config),
        limit=limit,
        run_id=run_id,
        apply=log is not None,
        log=log,
    )


def log_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def decisions(events):
    return [event.decision for event in events if isinstance(event, MessageDecided)]


class PagedMailbox(FakeMailbox):
    """Returns at most two IDs per page, as a small Gmail page would."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.list_calls = 0

    def list_unprocessed(self, page_token=None, page_size=100):
        self.list_calls += 1
        return super().list_unprocessed(page_token, min(page_size, 2))


def test_event_sequence_for_a_dry_run():
    mailbox = FakeMailbox([make_email("m1"), make_email("m2")])

    events = list(start(mailbox))

    assert events[0] == RunStarted("r1", dry_run=True, engine="fake", total=2)
    assert [type(event) for event in events[1:3]] == [MessageDecided, MessageDecided]
    assert events[3] == RunFinished("r1", decided=2, failed=0)


def test_the_limit_holds_across_pages():
    mailbox = PagedMailbox([make_email(f"m{n}") for n in range(7)])

    events = list(start(mailbox, limit=5))

    assert [d.message_id for d in decisions(events)] == ["m0", "m1", "m2", "m3", "m4"]
    assert mailbox.list_calls == 3


@pytest.mark.parametrize("limit, expected", [(None, 4), (3, 3), (6, 6)])
def test_limit_overrides_max_messages(limit, expected):
    mailbox = FakeMailbox([make_email(f"m{n}") for n in range(8)])

    events = list(start(mailbox, config=make_config(max_messages=4), limit=limit))

    assert events[0].total == expected


def test_processed_and_archived_mail_is_skipped():
    mailbox = FakeMailbox(
        [
            make_email("new"),
            make_email("done", labels=("INBOX", "Label_9")),
            make_email("old", labels=()),
        ],
        labels=[PROCESSED],
    )

    assert [d.message_id for d in decisions(start(mailbox))] == ["new"]


def test_a_confident_message_is_one_request_with_all_three_questions():
    engine = FakeEngine(confident)

    list(start(FakeMailbox([make_email()]), engine))

    assert len(engine.requests) == 1
    assert list(engine.requests[0].questions) == ["category", "needs_reply", "urgency"]
    assert "body" not in engine.requests[0].state


def test_low_confidence_retries_once_with_the_body_and_uses_the_second_answers():
    engine = FakeEngine(unsure_then_sure)
    mailbox = FakeMailbox([make_email()], bodies={"m1": "Your receipt for order 42"})

    [decision] = decisions(start(mailbox, engine))

    assert len(engine.requests) == 2
    assert engine.requests[1].state["body"] == "Your receipt for order 42"
    assert decision.used_body
    assert decision.confidence == 0.97 and not decision.low_confidence
    assert decision.probabilities.category == {"receipts": 0.97}  # the second response's


@pytest.mark.parametrize("bodies, body_chars", [({}, 2000), ({"m1": "Receipt"}, 0)])
def test_no_retry_without_a_body_to_add(bodies, body_chars):
    engine = FakeEngine(unsure_then_sure)
    mailbox = FakeMailbox([make_email()], bodies=bodies)

    [decision] = decisions(start(mailbox, engine, make_config(body_chars=body_chars)))

    assert len(engine.requests) == 1
    assert not decision.used_body and decision.low_confidence


def test_a_message_deleted_after_listing_fails_alone():
    mailbox = FakeMailbox([make_email("m1"), make_email("m2")])
    events = start(mailbox)

    assert isinstance(next(events), RunStarted)
    mailbox.emails.pop(0)  # m1 vanishes between listing and fetching
    rest = list(events)

    assert isinstance(rest[0], MessageFailed) and rest[0].message_id == "m1"
    assert rest[-1] == RunFinished("r1", decided=1, failed=1)


def test_other_mailbox_errors_end_the_run():
    class BrokenMailbox(FakeMailbox):
        def get_message(self, message_id):
            raise MailboxError("boom", status=500)

    with pytest.raises(MailboxError, match="boom"):
        list(start(BrokenMailbox([make_email()])))


def test_a_non_local_engine_is_refused_before_listing():
    engine = FakeEngine(confident)
    engine.runs_locally = False
    mailbox = PagedMailbox([make_email()])

    with pytest.raises(UserError, match="R-local-only"):
        list(start(mailbox, engine))
    assert mailbox.list_calls == 0


def test_a_dry_run_changes_no_labels(tmp_path):
    emails = [make_email("m1"), make_email("m2", labels=("INBOX",))]
    mailbox = FakeMailbox(emails)

    list(start(mailbox))

    assert mailbox.emails == emails
    assert (mailbox.label_ids_calls, mailbox.created, mailbox.modify_calls) == (0, [], [])
    assert list(tmp_path.iterdir()) == []


# Apply runs


def test_an_apply_run_gives_each_message_its_planned_labels_in_one_change(tmp_path):
    mailbox = FakeMailbox([make_email("m1"), make_email("m2")], labels=[RECEIPTS])
    log = RunLog(tmp_path / "runs.jsonl")

    events = list(start(mailbox, log=log))

    assert events[0] == RunStarted("r1", dry_run=False, engine="fake", total=2)
    assert events[-1] == RunFinished("r1", decided=2, failed=0)
    assert mailbox.created == ["Sorter", PROCESSED_LABEL]  # created once, on first use
    processed = mailbox.label_ids()[PROCESSED_LABEL]
    assert mailbox.modify_calls == [
        ("m1", ("Label_5", processed), ("INBOX", "UNREAD")),
        ("m2", ("Label_5", processed), ("INBOX", "UNREAD")),
    ]
    assert [email.label_ids for email in mailbox.emails] == [{"Label_5", processed}] * 2
    assert [line["id"] for line in log_lines(tmp_path / "runs.jsonl")] == ["m1", "m2"]
    assert list(start(mailbox, log=log, run_id="r2"))[0].total == 0  # R-skip-processed


def test_an_apply_run_logs_and_adds_only_labels_the_message_lacks(tmp_path):
    mailbox = FakeMailbox(
        [make_email("m1", labels=("INBOX", "UNREAD", "Label_5"))], labels=[RECEIPTS, PROCESSED]
    )

    list(start(mailbox, log=RunLog(tmp_path / "runs.jsonl")))

    [line] = log_lines(tmp_path / "runs.jsonl")
    assert (line["add"], line["add_ids"]) == ([PROCESSED_LABEL], ["Label_9"])
    assert mailbox.modify_calls == [("m1", ("Label_9",), ("INBOX", "UNREAD"))]


def test_a_failed_change_leaves_its_logged_message_unprocessed(tmp_path):
    mailbox = FakeMailbox([make_email("m1"), make_email("m2")], labels=[RECEIPTS, PROCESSED])
    mailbox.fail_modify = {"m2"}
    log = RunLog(tmp_path / "runs.jsonl")

    with pytest.raises(MailboxError):
        list(start(mailbox, log=log))

    assert [line["id"] for line in log_lines(tmp_path / "runs.jsonl")] == ["m1", "m2"]
    assert "Label_9" not in mailbox.emails[1].label_ids
    mailbox.fail_modify = set()
    assert [d.message_id for d in decisions(start(mailbox, log=log, run_id="r2"))] == ["m2"]


def test_a_label_that_cannot_be_created_writes_no_line_and_no_change(tmp_path):
    class NoCreate(FakeMailbox):
        def create_label(self, name):
            raise MailboxError("invalid label name", status=400)

    mailbox = NoCreate([make_email("m1")], labels=[RECEIPTS])

    with pytest.raises(MailboxError, match="invalid label name"):
        list(start(mailbox, log=RunLog(tmp_path / "runs.jsonl")))

    assert log_lines(tmp_path / "runs.jsonl") == []
    assert mailbox.modify_calls == []


def test_a_message_deleted_before_its_change_fails_alone(tmp_path):
    class Vanishing(FakeMailbox):
        def modify(self, message_id, add_label_ids, remove_label_ids):
            if message_id == "m1":
                raise MailboxError("not found", status=404)
            super().modify(message_id, add_label_ids, remove_label_ids)

    mailbox = Vanishing([make_email("m1"), make_email("m2")], labels=[RECEIPTS, PROCESSED])

    events = list(start(mailbox, log=RunLog(tmp_path / "runs.jsonl")))

    assert events[1] == MessageFailed("m1", "not found")
    assert events[-1] == RunFinished("r1", decided=1, failed=1)


def test_an_unwritable_log_ends_the_run_before_the_change(tmp_path):
    mailbox = FakeMailbox([make_email("m1")], labels=[RECEIPTS, PROCESSED])

    with pytest.raises(RunLogError):
        list(start(mailbox, log=RunLog(tmp_path / "missing" / "runs.jsonl")))

    assert mailbox.modify_calls == []


def test_an_apply_run_without_a_log_is_a_programming_error():
    config = make_config()
    events = run(
        FakeMailbox(), FakeEngine(confident), config, build_questions(config),
        limit=None, run_id="r1", apply=True,
    )  # fmt: skip

    with pytest.raises(ValueError, match="needs a run log"):
        next(events)


def test_run_id_is_a_utc_timestamp():
    assert new_run_id(datetime(2026, 10, 18, 14, 2, 11, tzinfo=UTC)) == "20261018-140211"


def test_decide_message_retries_a_low_confidence_answer_with_the_body():
    config = make_config()
    engine = FakeEngine(unsure_then_sure)
    mailbox = FakeMailbox([make_email("m1")], bodies={"m1": "Your order has shipped"})

    email, decision = decide_message(mailbox, engine, config, build_questions(config), "m1")

    assert len(engine.requests) == 2
    assert email.body == "Your order has shipped"
    assert decision.used_body and decision.confidence == 0.97
