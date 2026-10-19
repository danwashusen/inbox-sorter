"""Triage policy: turn the engine's answers into the labels to add and remove."""

from inbox_sorter.config import Category, Config
from inbox_sorter.engine import ChoiceAnswer, DecisionResponse, NoulAnswer, ScoreAnswer
from inbox_sorter.gmail import PROCESSED_LABEL
from inbox_sorter.questions import CATEGORY, NEEDS_REPLY, URGENCY
from inbox_sorter.types import (
    LOW_CONFIDENCE_LABEL,
    NEEDS_REPLY_LABEL,
    TODAY_LABEL,
    Decision,
    Email,
    Probabilities,
)


def needs_body(response: DecisionResponse, config: Config) -> bool:
    """True when the chosen category is below its min_confidence (R-body-retry)."""
    answer, category = _category(response, config)
    return answer.confidence < category.min_confidence


def decide(email: Email, response: DecisionResponse, config: Config, used_body: bool) -> Decision:
    """Apply the PRD policy to the final answers for ``email``."""
    answer, category = _category(response, config)
    reply = response.answers[NEEDS_REPLY]
    urgency = response.answers[URGENCY]
    assert isinstance(reply, NoulAnswer) and isinstance(urgency, ScoreAnswer)

    needs_reply = reply.probability >= config.flags.needs_reply_min
    today = urgency.score >= config.flags.today_min_score
    low_confidence = answer.confidence < category.min_confidence

    actions = set(category.actions)
    if needs_reply or today:  # R-flagged-stay-in-inbox
        actions -= {"archive", "mark_read"}
    if low_confidence:  # R-low-confidence-label-only
        actions = {"label"}

    add = [category.gmail_label] if "label" in actions else []
    if needs_reply:
        add.append(NEEDS_REPLY_LABEL)
    if today:
        add.append(TODAY_LABEL)
    if low_confidence:
        add.append(LOW_CONFIDENCE_LABEL)
    add.append(PROCESSED_LABEL)

    # Only labels the message carries, so undo never re-adds one it lacked.
    remove = []
    if "archive" in actions and "INBOX" in email.label_ids:
        remove.append("INBOX")
    if "mark_read" in actions and "UNREAD" in email.label_ids:
        remove.append("UNREAD")

    return Decision(
        message_id=email.id,
        category=category.id,
        confidence=answer.confidence,
        needs_reply=needs_reply,
        today=today,
        low_confidence=low_confidence,
        used_body=used_body,
        add_labels=tuple(add),
        remove_labels=tuple(remove),
        probabilities=Probabilities(
            category=dict(answer.probabilities),
            needs_reply=reply.probability,
            urgency=tuple(urgency.probabilities),
        ),
    )


def _category(response: DecisionResponse, config: Config) -> tuple[ChoiceAnswer, Category]:
    answer = response.answers[CATEGORY]
    assert isinstance(answer, ChoiceAnswer)
    for category in config.categories:
        if category.id == answer.choice:
            return answer, category
    # An engine bug, not a user error: engines choose only from offered options.
    raise ValueError(f"engine chose {answer.choice!r}, which is not a configured category")
