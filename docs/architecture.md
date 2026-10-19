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
            cli.py  (Typer)                       ui/  (Rich dashboard, plain, questionary)
               │                                     ▲
               ▼                                     │ RunEvent
   setup.py   runner.py ──── triage.py ─────────────┘
      │          │   │          (pure decisions)
      │          │   └──────── runlog.py ──► runs.jsonl
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

Modules marked **(exists)** are built. The others are planned.

- `cli.py` **(exists)**: `setup` and `run`; `undo` comes with #11. Parses
  flags, builds the real Gmail client and decision engine, picks a
  reporter, maps errors to exit codes (C-exit-codes).
- `config.py` **(exists)**: loads and validates `inbox-sorter.toml` into
  typed dataclasses; writes it for `setup` (A-config-writer).
- `gmail.py` **(exists)**: a `Mailbox` protocol plus the Gmail API
  implementation. Lists unprocessed inbox IDs, fetches metadata or
  plain-text body, manages labels, and applies one modify call per
  message. `LabelResolver` maps label names to IDs.
- `types.py` **(exists)**: the core types below.
- `errors.py` **(exists)**: `UserError`, the base of every expected
  failure the CLI reports as one line.
- `storage.py` **(exists)**: finds and creates the state directory.
- `engine.py` **(exists)**: the `DecisionEngine` protocol and
  engine-neutral request and answer types.
- `engines/` **(exists)**: one adapter per engine, chosen by
  `make_engine(kind)`. Only adapters import engine libraries such as torch
  or an HTTP client.
- `state.py` **(exists)**: turns an email into the state an engine reads.
- `questions.py` **(exists)**: builds the category, `needs_reply` and
  `urgency` questions from config, in engine-neutral form.
- `triage.py` **(exists)**: pure functions from answers and config to a
  `Decision`. Holds every policy rule: thresholds, body retry, flags, and
  keeping flagged mail in the inbox.
- `runner.py` **(exists)**: the run loop. Fetch, classify, retry,
  decide, log, apply. Emits `RunEvent`s and checks a stop flag between
  messages. So far it has no stop flag.
- `runlog.py` **(exists)**: one JSON line per applied decision, for `undo`.
- `summary.py` **(exists)**: tallies `RunEvent`s into the run summary.
- `setup.py` **(exists)**: re-sync, the wizard and the accuracy check.
- `ui/dashboard.py`: renders `RunEvent`s as the live panel.
- `ui/plain.py`, `ui/table.py`, `ui/prompts.py`, `ui/bars.py`,
  `ui/accuracy.py` and `ui/summary.py` **(exist)**: plain lines, the run
  table shown until the panel lands, `setup` prompts, category bars, the
  accuracy report and the run summary.
- `system.py`: `caffeinate` and notifications via subprocess.

## Core types

Plain frozen dataclasses in `types.py`, shared by every module.

- `Email`: id, sender, subject, snippet, label IDs, and body when fetched.
- `Probabilities`: the category, `needs_reply` and `urgency` probabilities.
- `Decision`: message ID, category, confidence, the two flags, labels to
  add and remove, low confidence, body use and `Probabilities`.
- `RunEvent`: `RunStarted`, `MessageDecided`, `MessageFailed` and
  `RunFinished` so far; a retrying variant comes with retries.

## Run loop

The loop lists IDs first (A-run-events), then works through them in order:

1. `gmail` lists inbox IDs without `Sorter/_processed`, up to the cap.
2. `gmail` fetches metadata; the engine answers the three questions.
3. If `triage` says confidence is low, `gmail` fetches the body and the
   engine answers again (R-body-retry).
4. `triage` returns the `Decision`.
5. A dry run stops here; an apply run resolves labels, then `runlog` logs.
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

v1 has no engine setting: `run` and `setup` use clef-local (A-fixed-engine).

Switching engines has three consequences the design accounts for:

- **Calibration.** Probabilities from different engines are not
  comparable, so thresholds may need retuning. `setup`'s accuracy report
  runs against the engine in use to show that.
- **Audit.** Each run log line records the engine `name`, so `undo` and
  later analysis know which engine decided.
- **Privacy.** A hosted engine such as Jev sends email content off the
  machine, which R-local-only and C-local-only forbid. `run` and `setup`
  refuse one (`runner.require_local`) until those rules are amended.

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

State lives in `~/Library/Application Support/inbox-sorter/`: `token.json`
and `client_secret.json` for OAuth, and `runs.jsonl`, the log for `undo`.

The config file, `inbox-sorter.toml`, sits in the current directory so it
can be versioned separately from state.

## Testing seams

Per the constitution's testing rules, unit tests never load clef or call
Gmail (C-fast-tests).

- `FakeMailbox` **(exists)**, in `tests/fake_mailbox.py`, holds messages, bodies
  and labels in memory; no config picks a mailbox, so it is test-only.
- `tests/mailbox_contract.py` **(exists)** checks any `Mailbox` against the
  read contract. It runs on `FakeMailbox` by default, and on the real
  inbox with `uv run pytest -m gmail`.
- `FakeEngine` **(exists)** returns scripted or uniform answers and
  records every request.
- Most tests target `triage`, `config`, `setup`, `runlog` and the runner
  loop with both fakes. Engine adapters and the Gmail client get
  marker-gated tests only (C-slow-tests-opt-in).
- `tests/engine_contract.py` **(exists)** checks any `DecisionEngine` returns a
  typed answer per question with probabilities summing to 1. It runs on
  `FakeEngine` by default, and on clef with `uv run pytest -m model`.

## Dependencies

New runtime dependencies, each with its reason (C-few-dependencies):

- `google-api-python-client` and `google-auth-oauthlib`: Gmail API and
  OAuth.
- `typer`, `rich` and `questionary`: the CLI, dashboard and prompts
  chosen in the UX spec.

## Open questions

- Should `undo` also remove `Sorter/_processed`, so undone mail is sorted
  again next run, or keep it so undone mail stays put?
- Would batching several emails per clef forward pass raise throughput
  enough to justify the extra code? Measure before deciding. If so,
  `DecisionEngine` gains a `decide_many`.
- Should a remote engine ever be allowed, for example Jev with an
  explicit opt-in? That is a product and constitution change, not an
  architecture one.

## Decisions

Accepted items move here with an `A-<slug>` ID.

- A-mailbox-seam: Gmail access goes through the `Mailbox` protocol in
  `gmail.py`, with `GmailMailbox` and a test-only `FakeMailbox`. Reads
  are `list_unprocessed`, `list_by_label`, `get_message`, `get_body` and
  `list_labels` and `label_ids`; writes are `create_label` and `modify`,
  by label ID. Gmail calls raise `MailboxError`, never a Google
  exception, with any HTTP status. The Google libraries are imported only
  inside the functions that call Gmail, so the core never loads them.
- A-core-types: `Email`, `Decision` and the `RunEvent` variants
  `RunStarted`, `MessageDecided`, `MessageFailed` and `RunFinished` are
  frozen dataclasses in `types.py`. `Decision` holds label names,
  resolved to IDs when applied. `RunEvent` variants are only added.
- A-typed-config: `load_config` in `config.py` reads `inbox-sorter.toml`
  with the standard `tomllib` into frozen dataclasses. Omitted `[run]`
  and `[flags]` keys take their defaults; every category key is
  required, and an unknown key or table is an error.
- A-state-dir: State lives in
  `~/Library/Application Support/inbox-sorter/`, created with mode
  `0o700` by `storage.state_dir`. The OAuth token is written with mode
  `0o600`.
- A-error-reporting: Expected failures subclass `UserError` in
  `errors.py`. The CLI entry point prints any `UserError` as
  `error: <message>` and exits 1, or shows the traceback with `--debug`,
  so commands raise rather than handle reporting themselves.
- A-config-location: `setup` and `run` read `inbox-sorter.toml` from the
  current directory; `--config PATH` overrides it
  (`config.resolve_config_path`).
- A-fixed-engine: no `[engine]` table; `run` and `setup` use clef-local.
- A-run-events: `runner.run` yields `RunEvent`s for any reporter. It lists
  IDs first, so `RunStarted` has the total. Only a 404 fails one message;
  other Gmail errors end the run. It refuses a non-local engine.
- A-config-writer: `config.write_config` rewrites the file, no comments,
  via a temp file that must load back equal; it refuses changed values.
- A-setup-prompter: `run_setup` asks through a `Prompter` (`ui/prompts.py`).
- A-accuracy-check: `check_accuracy` decides as `run` does, through
  `runner.decide_message`; a guess is right if the message has its label.
- A-category-bars: `ui/bars.py` draws the bars for setup and the dashboard.
- A-run-log: apply runs alone log, to `runs.jsonl`: a synced line, one modify.
- A-label-resolver: once per run; creates ancestors first; exact-case (#19).
- A-run-summary: `summary.SummaryTally` tallies; `ui/summary.py` renders.
