"""Checks every DecisionEngine must pass, whatever engine is behind it."""

import math

from inbox_sorter.engine import (
    Choice,
    ChoiceAnswer,
    DecisionEngine,
    DecisionRequest,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
)

TOLERANCE = 1e-3  # engines may round probabilities

CONTRACT_REQUEST = DecisionRequest(
    state={"from": "Sarah <sarah@example.com>", "subject": "Dinner on Friday?", "snippet": "Free?"},
    questions={
        "category": Choice({"personal": "Written by a person", "newsletter": "Bulk mailing"}),
        "needs_reply": Noul("Does the sender expect a reply?"),
        "urgency": Score(("Can wait", "This week", "Today")),
    },
)


def assert_conforms(engine: DecisionEngine, request: DecisionRequest = CONTRACT_REQUEST) -> None:
    response = engine.decide(request)

    assert response.engine == engine.name
    assert set(response.answers) == set(request.questions)
    for question_id, question in request.questions.items():
        answer = response.answers[question_id]
        if isinstance(question, Choice):
            assert isinstance(answer, ChoiceAnswer)
            assert set(answer.probabilities) == set(question.options)
            assert answer.choice in question.options
            assert math.isclose(sum(answer.probabilities.values()), 1, abs_tol=TOLERANCE)
        elif isinstance(question, Score):
            assert isinstance(answer, ScoreAnswer)
            assert len(answer.probabilities) == len(question.levels)
            assert math.isclose(sum(answer.probabilities), 1, abs_tol=TOLERANCE)
            assert 0 <= answer.score <= len(question.levels) - 1
        else:
            assert isinstance(answer, NoulAnswer)
            assert 0 <= answer.probability <= 1
