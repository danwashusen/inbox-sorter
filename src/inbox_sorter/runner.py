"""The run loop: list unprocessed mail, decide each message, and yield RunEvents.

A dry run changes nothing. An apply run logs each decision, then makes
one Gmail change per message (C-dry-run-first). A requested stop ends the
run before the next message, and transient Gmail errors are retried with
backoff, each wait announced by a Retrying event (A-long-runs).
"""

from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from time import sleep

from inbox_sorter.config import Config
from inbox_sorter.engine import DecisionEngine, DecisionRequest, Question
from inbox_sorter.errors import UserError
from inbox_sorter.gmail import MAX_PAGE_SIZE, LabelResolver, Mailbox, MailboxError
from inbox_sorter.runlog import RunLog
from inbox_sorter.state import message_state
from inbox_sorter.stop import StopFlag
from inbox_sorter.triage import decide, needs_body
from inbox_sorter.types import (
    Decision,
    Email,
    MessageDecided,
    MessageFailed,
    Retrying,
    RunEvent,
    RunFinished,
    RunStarted,
    RunStopped,
)

# Seconds to wait before each retry of one Gmail step: 10 retries, about
# 5 minutes in all, then the error ends the run (R-retry-transient).
RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0, 60.0)


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
    all_mail: bool = False,
    stop: StopFlag | None = None,
    retry_delays: Sequence[float] = RETRY_DELAYS,
) -> Iterator[RunEvent]:
    """Decide up to ``limit`` (else ``max_messages``) unprocessed inbox messages.

    With ``all_mail``, every unprocessed message listed at the start. With
    ``apply``, each decision is appended to ``log`` and then applied before
    it is yielded; otherwise nothing is written anywhere. A ``stop``
    requested mid-run ends it with RunStopped once the current message is done.
    """
    if apply and log is None:
        raise ValueError("an apply run needs a run log")
    if all_mail and limit is not None:
        raise ValueError("all_mail and limit cannot be used together")
    require_local(engine)

    if all_mail:
        cap = None
    else:
        cap = limit if limit is not None else config.run.max_messages
    ids: list[str] = []
    seen: set[str] = set()
    page_token: str | None = None
    while cap is None or len(ids) < cap:
        size = MAX_PAGE_SIZE if cap is None else min(cap - len(ids), MAX_PAGE_SIZE)
        page, page_token = yield from _retrying(
            lambda: mailbox.list_unprocessed(page_token, page_size=size), None, retry_delays
        )
        # Mail arriving between pages shifts the list, so an ID can come back.
        new = [message_id for message_id in page if message_id not in seen]
        ids.extend(new if cap is None else new[: cap - len(ids)])
        seen.update(new)
        if page_token is None:
            break

    yield RunStarted(run_id, dry_run=not apply, engine=engine.name, total=len(ids))
    resolver = LabelResolver(mailbox) if apply else None
    decided = failed = 0
    for message_id in ids:
        if stop is not None and stop.requested:
            yield RunStopped(run_id, decided, failed, remaining=len(ids) - decided - failed)
            return
        try:
            email, decision = yield from _retrying(
                lambda: decide_message(mailbox, engine, config, questions, message_id),
                message_id,
                retry_delays,
            )
            if resolver is not None and log is not None:
                yield from _apply(
                    mailbox, resolver, log, run_id, engine.name, email, decision, retry_delays
                )
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
    retry_delays: Sequence[float],
) -> Generator[Retrying]:
    """Resolve labels, log the line, then make the message's one Gmail change.

    Resolving first means a label that cannot be created ends the run before
    the line exists. Only labels the message lacks are logged and added, so
    undo never removes one the user applied. Resolving and the change are
    retried; the log line is written once, so a retried change never logs twice.
    """
    resolved = yield from _retrying(
        lambda: {name: resolver.id_for(name) for name in decision.add_labels},
        decision.message_id,
        retry_delays,
    )
    added = {
        name: label_id for name, label_id in resolved.items() if label_id not in email.label_ids
    }
    log.append(run_id, engine_name, decision, added)
    if added or decision.remove_labels:  # Gmail refuses a change with nothing in it
        yield from _retrying(
            lambda: mailbox.modify(
                decision.message_id, tuple(added.values()), decision.remove_labels
            ),
            decision.message_id,
            retry_delays,
        )


def _retrying[T](
    call: Callable[[], T], message_id: str | None, delays: Sequence[float]
) -> Generator[Retrying, None, T]:
    """Return ``call()``, retrying a transient MailboxError once per delay.

    Yields Retrying before each wait. A requested stop never cuts a wait
    short: the current message still finishes (ux.md §Stopping). Any other
    error, or a transient one after the last delay, propagates.
    """
    for attempt, delay in enumerate(delays, start=1):
        try:
            return call()
        except MailboxError as error:
            if not error.transient:
                raise
            yield Retrying(message_id, attempt, len(delays), float(delay), str(error))
            sleep(delay)
    return call()


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
