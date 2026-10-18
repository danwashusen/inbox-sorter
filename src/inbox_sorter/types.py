"""Core types shared by every module: emails, decisions and run events."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Email:
    id: str
    sender: str
    subject: str
    snippet: str
    label_ids: frozenset[str]  # Gmail label IDs
    body: str | None = None  # None until fetched


@dataclass(frozen=True)
class Decision:
    message_id: str
    category: str
    confidence: float
    needs_reply: bool
    today: bool
    low_confidence: bool
    used_body: bool
    # Label names, INBOX and UNREAD included; resolved to IDs when applied.
    add_labels: tuple[str, ...]
    remove_labels: tuple[str, ...]


@dataclass(frozen=True)
class RunStarted:
    run_id: str
    dry_run: bool
    engine: str
    total: int | None  # None when the backlog size is unknown


@dataclass(frozen=True)
class MessageDecided:
    email: Email
    decision: Decision


@dataclass(frozen=True)
class MessageFailed:
    message_id: str
    error: str


@dataclass(frozen=True)
class RunFinished:
    run_id: str
    decided: int
    failed: int


RunEvent = RunStarted | MessageDecided | MessageFailed | RunFinished
