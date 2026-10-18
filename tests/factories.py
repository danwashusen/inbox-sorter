"""Builders for configs, emails and engine answers used across tests."""

from dataclasses import replace

from inbox_sorter.config import Category, Config, FlagSettings, RunSettings
from inbox_sorter.engine import Answer, ChoiceAnswer, DecisionResponse, NoulAnswer, ScoreAnswer
from inbox_sorter.types import Email

RECEIPTS = Category(
    id="receipts",
    gmail_label="Receipts",
    source="imported",
    enabled=True,
    description="Order confirmation, shipping update, invoice or receipt",
    actions=("label", "archive", "mark_read"),
    min_confidence=0.85,
)
ALERTS = Category(
    id="alerts",
    gmail_label="Sorter/Alerts",
    source="created",
    enabled=True,
    description="Automated alert or notice from a service",
    actions=("label",),
    min_confidence=0.0,
)


def make_config(*categories: Category, **run: int) -> Config:
    return Config(
        run=RunSettings(**run),
        flags=FlagSettings(needs_reply_min=0.7, today_min_score=1.5),
        categories=categories or (RECEIPTS, ALERTS),
    )


def make_email(id: str = "m1", labels: tuple[str, ...] = ("INBOX", "UNREAD"), **fields) -> Email:
    email = Email(
        id=id,
        sender="Shop <orders@shop.example>",
        subject=f"Order {id}",
        snippet="Thanks for your order",
        label_ids=frozenset(labels),
    )
    return replace(email, **fields)


def answers(
    category: str = "receipts", confidence: float = 0.97, reply: float = 0.1, urgency: float = 0.2
) -> dict[str, Answer]:
    return {
        "category": ChoiceAnswer(category, confidence, {category: confidence}),
        "needs_reply": NoulAnswer(reply),
        "urgency": ScoreAnswer(urgency, (0.8, 0.2, 0.0)),
    }


def response(**kwargs) -> DecisionResponse:
    return DecisionResponse(engine="fake", answers=answers(**kwargs))
