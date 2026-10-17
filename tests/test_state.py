from inbox_sorter.state import email_state


def test_keeps_only_engine_fields():
    email = {"from": "a@example.com", "subject": "Hi", "snippet": "Hello", "id": "123"}

    assert email_state(email) == {"from": "a@example.com", "subject": "Hi", "snippet": "Hello"}


def test_missing_fields_become_empty_strings():
    assert email_state({"subject": "Hi"}) == {"from": "", "subject": "Hi", "snippet": ""}
