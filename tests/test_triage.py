from dataclasses import replace

import pytest

from inbox_sorter.triage import decide, needs_body
from tests.factories import RECEIPTS, make_config, make_email, response

CONFIG = make_config()


def test_confident_receipt_is_labelled_archived_and_marked_read():
    decision = decide(make_email(), response(), CONFIG, used_body=False)

    assert decision.category == "receipts"
    assert decision.confidence == 0.97
    assert (decision.needs_reply, decision.today, decision.low_confidence) == (False, False, False)
    assert decision.add_labels == ("Receipts", "Sorter/_processed")
    assert decision.remove_labels == ("INBOX", "UNREAD")


def test_an_already_read_message_keeps_its_read_state():
    decision = decide(make_email(labels=("INBOX",)), response(), CONFIG, used_body=False)

    assert decision.remove_labels == ("INBOX",)


def test_needs_reply_at_the_threshold_flags_and_stays_in_the_inbox():
    decision = decide(make_email(), response(reply=0.7), CONFIG, used_body=False)

    assert decision.needs_reply
    assert decision.add_labels == ("Receipts", "Sorter/Needs reply", "Sorter/_processed")
    assert decision.remove_labels == ()


def test_needs_reply_just_below_the_threshold_adds_no_flag():
    decision = decide(make_email(), response(reply=0.69), CONFIG, used_body=False)

    assert not decision.needs_reply
    assert decision.add_labels == ("Receipts", "Sorter/_processed")


def test_urgency_at_today_min_score_flags_today_and_stays_in_the_inbox():
    decision = decide(make_email(), response(urgency=1.5), CONFIG, used_body=False)

    assert decision.today
    assert decision.add_labels == ("Receipts", "Sorter/Today", "Sorter/_processed")
    assert decision.remove_labels == ()


def test_low_confidence_is_label_only_plus_the_marker():
    decision = decide(make_email(), response(confidence=0.5), CONFIG, used_body=True)

    assert decision.low_confidence and decision.used_body
    assert decision.add_labels == ("Receipts", "Sorter/_low-confidence", "Sorter/_processed")
    assert decision.remove_labels == ()


def test_low_confidence_applies_the_label_even_when_the_category_does_not():
    archive_only = replace(RECEIPTS, actions=("archive",))
    decision = decide(
        make_email(), response(confidence=0.5), make_config(archive_only), used_body=False
    )

    assert decision.add_labels == ("Receipts", "Sorter/_low-confidence", "Sorter/_processed")
    assert decision.remove_labels == ()


def test_needs_body_is_true_only_below_min_confidence():
    assert needs_body(response(confidence=0.84), CONFIG)
    assert not needs_body(response(confidence=0.85), CONFIG)


def test_an_unknown_category_choice_is_an_engine_error():
    with pytest.raises(ValueError, match="engine chose 'other'"):
        decide(make_email(), response(category="other"), CONFIG, used_body=False)
