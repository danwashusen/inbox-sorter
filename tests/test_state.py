from inbox_sorter.state import email_state, message_state
from inbox_sorter.types import Email


def test_keeps_only_engine_fields():
    email = {"from": "a@example.com", "subject": "Hi", "snippet": "Hello", "id": "123"}

    assert email_state(email) == {"from": "a@example.com", "subject": "Hi", "snippet": "Hello"}


def test_missing_fields_become_empty_strings():
    assert email_state({"subject": "Hi"}) == {"from": "", "subject": "Hi", "snippet": ""}


def test_message_state_maps_email_fields():
    email = Email("m1", "a@example.com", "Hi", "Hello", frozenset({"INBOX"}))

    assert message_state(email) == {"from": "a@example.com", "subject": "Hi", "snippet": "Hello"}


def test_message_state_adds_the_body_once_fetched():
    email = Email("m1", "a@example.com", "Hi", "Hello", frozenset(), body="Full text")

    assert message_state(email)["body"] == "Full text"
