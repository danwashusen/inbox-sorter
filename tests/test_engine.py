import pytest

from inbox_sorter.engine import Choice, DecisionRequest, Noul, Score


def test_request_accepts_all_question_types():
    request = DecisionRequest(
        state={"subject": "Hi"},
        questions={
            "category": Choice({"personal": "Written by a person"}),
            "urgency": Score(("Can wait", "Today")),
            "needs_reply": Noul(),
        },
    )

    assert set(request.questions) == {"category", "urgency", "needs_reply"}


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        ({}, "at least one question"),
        ({"q": Choice({})}, "q: a choice needs at least one option"),
        ({"q": Score(("Only one",))}, "q: a score needs at least two levels"),
    ],
)
def test_request_rejects_questions_no_engine_can_answer(questions, message):
    with pytest.raises(ValueError, match=message):
        DecisionRequest(state={}, questions=questions)
