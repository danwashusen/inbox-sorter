"""Convert neutral requests and answers to and from System One bodies.

System One is the request and response format clef's ``systemone``
function and hosted System One APIs share. Keeping the conversion here
lets every System One engine reuse it, and keeps it testable without a
model.
"""

from typing import Any

from inbox_sorter.engine import (
    Answer,
    Choice,
    ChoiceAnswer,
    DecisionRequest,
    DecisionResponse,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
)


def to_request_body(request: DecisionRequest, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "state": request.state,
        "questions": {
            question_id: _question_body(question)
            for question_id, question in request.questions.items()
        },
    }


def _question_body(question: Question) -> dict[str, Any]:
    if isinstance(question, Choice):
        body: dict[str, Any] = {"type": "choice", "criteria": dict(question.options)}
    elif isinstance(question, Score):
        body = {"type": "score", "criteria": list(question.levels)}
    else:
        body = {"type": "noul"}
    # Clef falls back to the question ID when instructions are absent.
    if question.instructions:
        body["instructions"] = question.instructions
    return body


def from_response_body(
    request: DecisionRequest, body: dict[str, Any], engine: str
) -> DecisionResponse:
    answers = body.get("answers", {})
    return DecisionResponse(
        engine=engine,
        answers={
            question_id: _answer(question_id, question, answers)
            for question_id, question in request.questions.items()
        },
    )


def _answer(question_id: str, question: Question, answers: dict[str, Any]) -> Answer:
    if question_id not in answers:
        raise ValueError(f"{question_id}: missing from engine response")
    raw = answers[question_id]
    expected = {Choice: "choice", Score: "score", Noul: "noul"}[type(question)]
    if raw.get("type") != expected:
        raise ValueError(f"{question_id}: expected a {expected} answer, got {raw.get('type')!r}")
    if isinstance(question, Choice):
        return ChoiceAnswer(
            choice=raw["choice"],
            confidence=raw["confidence"],
            probabilities=dict(raw["probabilities"]),
        )
    if isinstance(question, Score):
        levels = range(len(question.levels))
        return ScoreAnswer(
            score=raw["score"],
            probabilities=tuple(raw["probabilities"][str(level)] for level in levels),
        )
    return NoulAnswer(probability=raw["noul"])
