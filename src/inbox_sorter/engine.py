"""Engine-neutral decision requests, answers and the DecisionEngine protocol.

The core asks typed questions about a state and gets typed answers back.
Which engine answers (clef in process today, others later) is hidden
behind DecisionEngine; adapters live in ``inbox_sorter.engines``.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Choice:
    """Pick one option. Maps option ID to the description it is judged by."""

    options: dict[str, str]
    instructions: str = ""


@dataclass(frozen=True)
class Score:
    """Place the state on ordered levels, lowest first."""

    levels: tuple[str, ...]
    instructions: str = ""


@dataclass(frozen=True)
class Noul:
    """A yes/no question answered with a probability."""

    instructions: str = ""


Question = Choice | Score | Noul


@dataclass(frozen=True)
class DecisionRequest:
    state: dict[str, Any]
    questions: dict[str, Question]

    def __post_init__(self) -> None:
        # Fail before any engine is called, with the question ID in the message.
        if not self.questions:
            raise ValueError("at least one question is required")
        for question_id, question in self.questions.items():
            if isinstance(question, Choice) and not question.options:
                raise ValueError(f"{question_id}: a choice needs at least one option")
            if isinstance(question, Score) and len(question.levels) < 2:
                raise ValueError(f"{question_id}: a score needs at least two levels")


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class ScoreAnswer:
    score: float  # expected level index, 0 to len(levels) - 1
    probabilities: tuple[float, ...]  # one per level, in level order


@dataclass(frozen=True)
class NoulAnswer:
    probability: float  # probability the answer is yes


Answer = ChoiceAnswer | ScoreAnswer | NoulAnswer


@dataclass(frozen=True)
class DecisionResponse:
    engine: str
    answers: dict[str, Answer]


class DecisionEngine(Protocol):
    name: str
    runs_locally: bool  # False if the state leaves this machine

    def decide(self, request: DecisionRequest) -> DecisionResponse: ...

    def close(self) -> None: ...
