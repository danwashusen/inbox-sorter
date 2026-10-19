from datetime import UTC, datetime

import pytest

from inbox_sorter.engines.fake import FakeEngine
from inbox_sorter.errors import UserError
from inbox_sorter.gmail import PROCESSED_LABEL, LabelInfo, MailboxError
from inbox_sorter.questions import build_questions
from inbox_sorter.runner import decide_message, new_run_id, run
from inbox_sorter.types import MessageDecided, MessageFailed, RunFinished, RunStarted
from tests.factories import answers, make_config, make_email
from tests.fake_mailbox import FakeMailbox

PROCESSED = LabelInfo(id="Label_9", name=PROCESSED_LABEL, system=False, message_count=0)


def confident(request):
    return answers()


def unsure_then_sure(request):
    return answers(confidence=0.97 if "body" in request.state else 0.5, reply=0.9)


def start(mailbox, engine=None, config=None, limit=None):
    config = config or make_config()
    return run(
        mailbox,
        engine or FakeEngine(confident),
        config,
        build_questions(config),
        limit=limit,
        run_id="r1",
    )


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


def test_a_dry_run_changes_no_labels():
    emails = [make_email("m1"), make_email("m2", labels=("INBOX",))]
    mailbox = FakeMailbox(emails)

    list(start(mailbox))

    assert mailbox.emails == emails


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
