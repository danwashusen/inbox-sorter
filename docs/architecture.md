---
summary: >-
  Straw-man architecture for inbox-sorter: module map, core types, the run
  loop and its per-message atomic Gmail update, the swappable decision
  engine seam (clef today, Jev or others later), the Gmail seam, on-disk
  state, dependencies, and open questions.
---
# inbox-sorter architecture

Status: straw-man, for discussion. Nothing here is decided until it moves
to Decisions. Scope comes from the [product requirements](prd.md) and
[terminal UX spec](ux.md); rules come from the
[constitution](constitution.md).

## Overview

One local process. A thin CLI drives a core of plain functions. The core
talks to the outside world through two seams, Gmail and the decision
engine, so tests swap both for fakes and clef can be replaced later.

```text
            cli.py  (Typer, questionary)          ui/  (Rich dashboard, plain)
               │                                     ▲
               ▼                                     │ RunEvent
   setup.py   runner.py ──── triage.py ─────────────┘
      │          │   │          (pure decisions)
      │          │   └──────── runlog.py ──► runs/<run-id>.jsonl
      ▼          ▼
   gmail.py   engine.py  (DecisionEngine protocol, neutral types)
   (Gmail API)    │
                  ├─► engines/clef_local.py ──► clef on MPS (in process)
                  └─► engines/systemone_http.py ──► Jev, or clef on SGLang
                                                    (future, see below)
```

## Modules

All under `src/inbox_sorter/`. Only `cli.py` and `ui/` may import Typer,
Rich or questionary (C-thin-cli).

Built so far: `engine.py`, `engines/` and `state.py`, marked **(exists)**.
Every other module is planned and does not exist yet.

- `cli.py`: the `setup`, `run` and `undo` commands. Parses flags, builds
  the real Gmail client and decision engine, picks a reporter, maps errors
  to exit codes (C-exit-codes).
- `config.py`: loads and validates `inbox-sorter.toml` into typed
  dataclasses; writes it for `setup`.
- `gmail.py`: a `Mailbox` protocol plus the Gmail API implementation.
  Lists unprocessed inbox IDs, fetches metadata or plain-text body, manages
  labels, and applies one modify call per message.
- `engine.py` **(exists)**: the `DecisionEngine` protocol and
  engine-neutral request and answer types.
- `engines/` **(exists)**: one adapter per engine, chosen by
  `make_engine(kind)`. Only adapters import engine libraries such as torch
  or an HTTP client.
- `state.py` **(exists)**: turns an email into the state an engine reads.
- `questions.py`: builds the category, `needs_reply` and `urgency`
  questions from config, in engine-neutral form.
- `triage.py`: pure functions from answers and config to a `Decision`.
  Holds every policy rule: thresholds, body retry, flags, and keeping
  flagged mail in the inbox.
- `runner.py`: the run loop. Fetch, classify, retry, decide, apply, log.
  Emits `RunEvent`s and checks a stop flag between messages.
- `runlog.py`: appends one JSON line per decision; reads a run back for
  `undo`.
- `setup.py`: label filtering, re-sync rules and the accuracy report,
  as pure functions the CLI prompts around.
- `ui/dashboard.py` and `ui/plain.py`: reporters that render `RunEvent`s
  as the live panel or plain lines.
- `system.py`: `caffeinate` and notifications via subprocess.

## Core types

Plain frozen dataclasses in `types.py`, shared by every module.

- `Email`: id, sender, subject, snippet, label IDs, and body when fetched.
- `Answers`: category probabilities, `needs_reply` probability and
  `urgency` score, in engine-neutral form.
- `Decision`: category, labels to add, labels to remove, and whether it
  is low confidence or used the body.
- `RunEvent`: started, decided, retrying, error and finished variants,
  carrying the counts the dashboard needs.

## Run loop

For each message, in order:

1. `gmail` lists the next page of inbox IDs without `Sorter/_processed`.
2. `gmail` fetches metadata; the engine answers the three questions.
3. If `triage` says confidence is low, `gmail` fetches the body and the
   engine answers again (R-body-retry).
4. `triage` returns the `Decision`.
5. `runlog` appends the decision. In dry run, the loop stops here.
6. `gmail` applies one `messages.modify` call that adds the category,
   flag and `Sorter/_processed` labels and removes `INBOX` or `UNREAD`.

A single modify call per message makes each message all-or-nothing.
A crash leaves it unprocessed, so the next run retries it (R-resumable).
Writing the log before applying means `undo` never misses a change.

Gmail and engine work alternate, with no threads in v1. At about
2.6 s per message, the model dominates, and Gmail calls add little.

## Decision engines

The core never sees clef. It talks to one protocol:

```python
class DecisionEngine(Protocol):
    name: str            # e.g. "clef-local:Cloudflare/clef"
    runs_locally: bool   # False if state leaves the machine
    def decide(self, request: DecisionRequest) -> DecisionResponse: ...
    def close(self) -> None: ...
```

- `DecisionRequest` holds a state dict and named questions. A question is
  `Choice` (option ID to description), `Score` (ordered levels) or `Noul`
  (yes/no), each with instructions.
- `DecisionResponse` holds the engine name and one typed answer per
  question: `ChoiceAnswer`, `ScoreAnswer` or `NoulAnswer`, each with its
  probabilities.
- These types are ours. They follow the System One question model that
  clef and Jev share, but no engine's wire format leaks past its adapter.

Adapters:

- `engines/systemone.py` **(exists)**: converts neutral requests and
  answers to and from System One bodies. Shared by every System One
  engine and tested without a model.
- `engines/clef_local.py` **(exists)**: loads clef with its joint head in
  process on MPS. The only real engine in v1.
- `engines/fake.py` **(exists)**: scripted answers for tests.
- `engines/systemone_http.py`: future. Posts the same request to any
  System One endpoint: Jev's hosted API, or clef served by SGLang.

Config picks the engine:

```toml
[engine]
kind = "clef-local"          # later: "systemone-http"
model = "Cloudflare/clef"
```

Switching engines has three consequences the design accounts for:

- **Calibration.** Probabilities from different engines are not
  comparable, so thresholds may need retuning. `setup`'s accuracy report
  runs against the configured engine to show that.
- **Audit.** Each run log line records the engine `name`, so `undo` and
  later analysis know which engine decided.
- **Privacy.** A hosted engine such as Jev sends email content off the
  machine, which R-local-only and C-local-only forbid. `cli.py` refuses an
  engine with `runs_locally = False`. Allowing one needs those rules
  amended first.

## Gmail integration

- OAuth desktop flow with the `gmail.modify` scope (R-oauth-modify-scope).
  The client secret and token live in the state directory, never in the
  repo (C-no-secrets).
- Labels are resolved by name to IDs once per run and created when
  missing (R-create-missing-labels).
- Rate-limit and 5xx errors retry with exponential backoff and emit a
  `retrying` event (R-retry-transient).
- The body is the `text/plain` part, cut to `body_chars`. Mail with only
  HTML falls back to text extracted with the standard `html.parser`.

## Storage

State lives in `~/Library/Application Support/inbox-sorter/`:

- `token.json` and `client_secret.json` for OAuth.
- `runs/<run-id>.jsonl`, one file per run, for the log and `undo`.

The config file, `inbox-sorter.toml`, sits in the current directory so it
can be versioned separately from state.

## Testing seams

Per the constitution's testing rules, unit tests never load clef or call
Gmail (C-fast-tests).

- `FakeMailbox` holds messages and labels in memory and records modify
  calls.
- `FakeEngine` **(exists)** returns scripted or uniform answers and
  records every request.
- Most tests target `triage`, `config`, `setup`, `runlog` and the runner
  loop with both fakes. Engine adapters and the Gmail client get
  marker-gated tests only (C-slow-tests-opt-in).
- `tests/engine_contract.py` **(exists)** checks any `DecisionEngine`
  returns a typed answer per question with probabilities summing to 1.
  It runs on `FakeEngine` by default, and on clef with
  `uv run pytest -m model`.

## Dependencies

New runtime dependencies, each with its reason (C-few-dependencies):

- `google-api-python-client` and `google-auth-oauthlib`: Gmail API and
  OAuth.
- `typer`, `rich` and `questionary`: the CLI, dashboard and prompts
  chosen in the UX spec.

## Open questions

- Should `undo` also remove `Sorter/_processed`, so undone mail is sorted
  again next run, or keep it so undone mail stays put?
- Writing TOML needs `tomli-w` or a hand-written template. Is one small
  dependency worth it?
- Would batching several emails per clef forward pass raise throughput
  enough to justify the extra code? Measure before deciding. If so,
  `DecisionEngine` gains a `decide_many`.
- Should a remote engine ever be allowed, for example Jev with an
  explicit opt-in? That is a product and constitution change, not an
  architecture one.
- Should the config file default to the state directory instead of the
  current directory?

## Decisions

None yet. Accepted items move here with an `A-<slug>` ID.
