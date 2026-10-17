"""A scripted decision engine for tests. Loads no model."""

from collections.abc import Callable

from inbox_sorter.engine import (
    Answer,
    Choice,
    ChoiceAnswer,
    DecisionRequest,
    DecisionResponse,
    NoulAnswer,
    Score,
    ScoreAnswer,
)

Responder = Callable[[DecisionRequest], dict[str, Answer]]


def uniform_answers(request: DecisionRequest) -> dict[str, Answer]:
    """Answer every question with no preference: the first option wins ties."""
    answers: dict[str, Answer] = {}
    for question_id, question in request.questions.items():
        if isinstance(question, Choice):
            share = 1 / len(question.options)
            answers[question_id] = ChoiceAnswer(
                choice=next(iter(question.options)),
                confidence=share,
                probabilities={option: share for option in question.options},
            )
        elif isinstance(question, Score):
            count = len(question.levels)
            answers[question_id] = ScoreAnswer(
                score=(count - 1) / 2, probabilities=(1 / count,) * count
            )
        else:
            answers[question_id] = NoulAnswer(probability=0.5)
    return answers


class FakeEngine:
    name = "fake"
    runs_locally = True

    def __init__(self, respond: Responder = uniform_answers) -> None:
        self._respond = respond
        self.requests: list[DecisionRequest] = []

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        self.requests.append(request)
        return DecisionResponse(engine=self.name, answers=self._respond(request))

    def close(self) -> None:
        pass
