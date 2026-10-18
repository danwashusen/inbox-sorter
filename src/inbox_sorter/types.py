"""Core types and label names shared by every module: emails, decisions and run events."""

from dataclasses import dataclass

# The parent of every label the tool owns (prd.md §Gmail labels). Names
# under it starting with "_" are bookkeeping markers.
SORTER_PREFIX = "Sorter/"
NEEDS_REPLY_LABEL = SORTER_PREFIX + "Needs reply"
TODAY_LABEL = SORTER_PREFIX + "Today"
LOW_CONFIDENCE_LABEL = SORTER_PREFIX + "_low-confidence"

# Gmail's system labels; each name is also its ID. Setup skips them
# (prd.md §Setup), so no category may use one.
SYSTEM_LABELS: frozenset[str] = frozenset(
    {
        "INBOX",
        "SPAM",
        "TRASH",
        "UNREAD",
        "STARRED",
        "IMPORTANT",
        "SENT",
        "DRAFT",
        "CHAT",
        "CATEGORY_PERSONAL",
        "CATEGORY_SOCIAL",
        "CATEGORY_PROMOTIONS",
        "CATEGORY_UPDATES",
        "CATEGORY_FORUMS",
    }
)


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
