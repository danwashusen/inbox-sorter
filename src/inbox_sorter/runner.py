"""The run loop: list unprocessed mail, decide each message, and yield RunEvents.

A dry run changes nothing. An apply run logs each decision, then makes
one Gmail change per message (C-dry-run-first).
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

from inbox_sorter.config import Config
from inbox_sorter.engine import DecisionEngine, DecisionRequest, Question
from inbox_sorter.errors import UserError
from inbox_sorter.gmail import MAX_PAGE_SIZE, LabelResolver, Mailbox, MailboxError
from inbox_sorter.runlog import RunLog
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
    apply: bool = False,
    log: RunLog | None = None,
) -> Iterator[RunEvent]:
    """Decide up to ``limit`` (else ``max_messages``) unprocessed inbox messages.

    With ``apply``, each decision is appended to ``log`` and then applied
    before it is yielded; otherwise nothing is written anywhere.
    """
    if apply and log is None:
        raise ValueError("an apply run needs a run log")
    require_local(engine)

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

    yield RunStarted(run_id, dry_run=not apply, engine=engine.name, total=len(ids))
    resolver = LabelResolver(mailbox) if apply else None
    decided = failed = 0
    for message_id in ids:
        try:
            email, decision = decide_message(mailbox, engine, config, questions, message_id)
            if resolver is not None and log is not None:
                _apply(mailbox, resolver, log, run_id, engine.name, email, decision)
        except MailboxError as error:
            if error.status != 404:  # only a message deleted since listing fails alone
                raise
            failed += 1
            yield MessageFailed(message_id, str(error))
            continue
        decided += 1
        yield MessageDecided(email, decision)
    yield RunFinished(run_id, decided, failed)


def _apply(
    mailbox: Mailbox,
    resolver: LabelResolver,
    log: RunLog,
    run_id: str,
    engine_name: str,
    email: Email,
    decision: Decision,
) -> None:
    """Resolve labels, log the line, then make the message's one Gmail change.

    Resolving first means a label that cannot be created ends the run before
    the line exists. Only labels the message lacks are logged and added, so
    undo never removes one the user applied.
    """
    resolved = {name: resolver.id_for(name) for name in decision.add_labels}
    added = {
        name: label_id for name, label_id in resolved.items() if label_id not in email.label_ids
    }
    log.append(run_id, engine_name, decision, added)
    if added or decision.remove_labels:  # Gmail refuses a change with nothing in it
        mailbox.modify(decision.message_id, tuple(added.values()), decision.remove_labels)


def require_local(engine: DecisionEngine) -> None:
    """Refuse an engine that sends email content off this machine (C-local-only)."""
    if not engine.runs_locally:
        raise UserError(
            f"engine {engine.name} sends email content off this machine, which R-local-only forbids"
        )


def decide_message(
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
