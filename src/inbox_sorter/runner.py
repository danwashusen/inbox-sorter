"""The run loop: list unprocessed mail, decide each message, and yield RunEvents.

It changes nothing in Gmail; #10 applies the decisions it yields.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

from inbox_sorter.config import Config
from inbox_sorter.engine import DecisionEngine, DecisionRequest, Question
from inbox_sorter.errors import UserError
from inbox_sorter.gmail import MAX_PAGE_SIZE, Mailbox, MailboxError
from inbox_sorter.state import message_state
from inbox_sorter.triage import decide, needs_body
from inbox_sorter.types import (
    Decision,
    Email,
    MessageDecided,
    MessageFailed,
    RunEvent,
    RunFinished,
    RunStarted,
)


def new_run_id(now: datetime | None = None) -> str:
    """A UTC timestamp to the second, such as 20261018-140211."""
    return (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")


def run(
    mailbox: Mailbox,
    engine: DecisionEngine,
    config: Config,
    questions: dict[str, Question],
    *,
    limit: int | None,
    run_id: str,
) -> Iterator[RunEvent]:
    """Dry-run up to ``limit`` (else ``max_messages``) unprocessed inbox messages."""
    if not engine.runs_locally:
        raise UserError(
            f"engine {engine.name} sends email content off this machine, which R-local-only forbids"
        )

    cap = limit if limit is not None else config.run.max_messages
    ids: list[str] = []
    page_token: str | None = None
    while len(ids) < cap:
        page, page_token = mailbox.list_unprocessed(
            page_token, page_size=min(cap - len(ids), MAX_PAGE_SIZE)
        )
        ids.extend(page[: cap - len(ids)])
        if page_token is None:
            break

    yield RunStarted(run_id, dry_run=True, engine=engine.name, total=len(ids))
    decided = failed = 0
    for message_id in ids:
        try:
            email, decision = _decide(mailbox, engine, config, questions, message_id)
        except MailboxError as error:
            if error.status != 404:  # only a message deleted since listing fails alone
                raise
            failed += 1
            yield MessageFailed(message_id, str(error))
            continue
        decided += 1
        yield MessageDecided(email, decision)
    yield RunFinished(run_id, decided, failed)


def _decide(
    mailbox: Mailbox,
    engine: DecisionEngine,
    config: Config,
    questions: dict[str, Question],
    message_id: str,
) -> tuple[Email, Decision]:
    """Ask once over the metadata, and once more with the body when triage needs it."""
    email = mailbox.get_message(message_id)
    response = engine.decide(DecisionRequest(message_state(email), questions))
    used_body = False
    if needs_body(response, config) and config.run.body_chars > 0:
        body = mailbox.get_body(message_id, config.run.body_chars)
        if body:
            email = replace(email, body=body)
            response = engine.decide(DecisionRequest(message_state(email), questions))
            used_body = True
    return email, decide(email, response, config, used_body)
