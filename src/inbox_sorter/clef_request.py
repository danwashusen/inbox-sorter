"""Build clef SystemOne request bodies from emails and question schemas."""

from typing import Any

MODEL = "clef"
EMAIL_FIELDS = ("from", "subject", "snippet")
QUESTION_TYPES = {"noul", "choice", "score"}


def build_request(email: dict[str, str], questions: dict[str, Any]) -> dict[str, Any]:
    """Return a SystemOne request for one email.

    Raises ValueError for requests clef's ``systemone`` would reject, so bad
    config fails before the model is loaded.
    """
    if not questions:
        raise ValueError("at least one question is required")
    for question_id, question in questions.items():
        if question.get("type") not in QUESTION_TYPES:
            raise ValueError(f"{question_id}: type must be noul, choice, or score")
        if question["type"] != "noul" and not question.get("criteria"):
            raise ValueError(f"{question_id}: criteria must not be empty")
    state = {field: email.get(field, "") for field in EMAIL_FIELDS}
    return {"model": MODEL, "state": state, "questions": questions}
