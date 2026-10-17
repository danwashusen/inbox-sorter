---
summary: >-
  Non-negotiable engineering rules for inbox-sorter: simplicity and
  dependencies, code layout, terminal output and exit codes, safety and
  privacy, and the basic testing bar (fast unit tests, no coverage target).
---
# inbox-sorter constitution

These rules bind every change. If a requirement or plan conflicts with
one, raise the conflict instead of working around it. Change a rule only
through a PR that edits this file.

Each rule has a stable slug ID, `C-<slug>`, following the same
conventions as requirement IDs.

## Simplicity

- C-smallest-change: Build the smallest thing that meets the requirement.
  No abstractions, options or plugins for needs that do not exist yet.
- C-few-dependencies: Prefer the standard library. A new runtime
  dependency needs a one-line reason in its PR.
- C-uv-managed: uv manages Python and dependencies. `uv.lock` is committed
  and must pass `uv lock --check`.

## Code layout

- C-src-layout: Code lives in `src/inbox_sorter/`; tests live in
  `tests/test_<module>.py`.
- C-thin-cli: The CLI layer only parses arguments and renders output. All
  decisions live in plain functions that never import Typer or Rich, so
  they can be tested directly.
- C-typed: Public functions have type hints. Names say what things are;
  comments explain why, not what.

## Terminal behaviour

- C-plain-output: Every command works without a terminal. With `--plain`,
  or when output is not a terminal, it prints stable line-based text
  instead of Rich panels.
- C-exit-codes: Exit 0 on success, 1 on failure and 2 on bad usage.
- C-clear-errors: Expected errors print one actionable line, not a
  traceback. Tracebacks appear only with `--debug`.

## Safety and privacy

- C-dry-run-first: Nothing changes Gmail without an explicit flag
  (R-dry-run-default).
- C-local-only: Email content never leaves the machine except to Gmail
  itself (R-local-only).
- C-no-secrets: OAuth tokens and credentials are never committed or
  logged.

## Testing

The bar is basic, useful coverage, not a percentage.

- C-test-new-logic: Every new or changed function in the core gets a test
  for its main path and its most likely failure.
- C-bug-gets-test: Every bug fix adds a test that failed before the fix.
- C-fast-tests: `uv run pytest` loads no model and makes no network calls.
  Fake clef and Gmail at the edges. The suite runs in seconds.
- C-slow-tests-opt-in: Tests that need clef or Gmail carry a pytest
  marker and are skipped by default.
- C-no-coverage-gate: There is no coverage threshold. Do not test the CLI
  wiring, Rich layout or third-party code.

## Docs

- C-docs-with-code: A change to behaviour updates the PRD or UX spec in
  the same PR. Docs follow the conventions in `AGENTS.md` and pass
  `mdsmith check`.
