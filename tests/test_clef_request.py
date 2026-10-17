import pytest

from inbox_sorter.clef_request import build_request

EMAIL = {"from": "a@example.com", "subject": "Hi", "snippet": "Hello", "id": "123"}
CATEGORY = {"type": "choice", "criteria": {"personal": "Written by a person"}}


def test_builds_request_with_email_state_only():
    request = build_request(EMAIL, {"category": CATEGORY})

    assert request["model"] == "clef"
    assert request["state"] == {"from": "a@example.com", "subject": "Hi", "snippet": "Hello"}
    assert request["questions"] == {"category": CATEGORY}


def test_missing_email_fields_become_empty_strings():
    request = build_request({"subject": "Hi"}, {"category": CATEGORY})

    assert request["state"] == {"from": "", "subject": "Hi", "snippet": ""}


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        ({}, "at least one question"),
        ({"q": {"type": "essay"}}, "type must be"),
        ({"q": {"type": "choice"}}, "criteria must not be empty"),
        ({"q": {"type": "score", "criteria": []}}, "criteria must not be empty"),
    ],
)
def test_rejects_requests_clef_would_reject(questions, message):
    with pytest.raises(ValueError, match=message):
        build_request(EMAIL, questions)


def test_noul_needs_no_criteria():
    request = build_request(EMAIL, {"needs_reply": {"type": "noul"}})

    assert request["questions"]["needs_reply"]["type"] == "noul"
