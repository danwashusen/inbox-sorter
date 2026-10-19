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
class Probabilities:
    """The engine's final answers, for the run log (R-run-log)."""

    category: dict[str, float]  # every offered category ID to its probability
    needs_reply: float
    urgency: tuple[float, ...]  # one per urgency level, lowest first


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
    probabilities: Probabilities | None = None  # None only in hand-built test decisions


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


@dataclass(frozen=True)
class Retrying:
    """Yielded before each backoff wait on a transient Gmail error (R-retry-transient)."""

    message_id: str | None  # None while listing
    attempt: int  # 1 for the first retry
    attempts: int  # the most retries this step gets
    delay: float  # seconds
    error: str


@dataclass(frozen=True)
class RunStopped:
    """Ends a run stopped on request, in place of RunFinished (R-graceful-stop)."""

    run_id: str
    decided: int
    failed: int
    remaining: int  # listed but not reached; a rerun picks them up


RunEvent = RunStarted | MessageDecided | MessageFailed | RunFinished | Retrying | RunStopped
