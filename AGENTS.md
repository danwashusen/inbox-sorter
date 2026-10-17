# inbox-sorter

A local Python CLI that triages a Gmail inbox with the Cloudflare clef
decision model running on Apple Silicon (MPS).

## Commands

- `uv run pytest`: run the unit tests (fast; no model load).
- `uv run pytest -m model`: run the slow tests that load clef.
- `uv run python smoke_clef.py`: load clef and classify sample emails.
- `mise exec -- mdsmith check .`: lint all Markdown; `fix` auto-fixes.

## Doc conventions

Docs are edited on parallel branches, so write them to merge cleanly.

- Give requirements and other referenced items a slug ID, `R-<slug>`, such
  as R-dry-run-default. Never number them (`R1`, `R-1`): two branches adding
  a requirement would pick the same number. mdsmith's `occurrence` rule
  rejects numeric IDs.
- Write IDs as plain text, not in code spans, so the lint rule sees them.
- Never rename or reuse a slug. To retire an item, delete it and update
  every reference.
- Use one bullet per item for lists that grow, such as requirements and
  decisions. Avoid tables there: mdsmith pads every row to the widest
  cell, so one new row rewrites the whole table.

## Docs

Read a doc only when the task needs it. The index below is generated from
each file's `summary` front matter; edit the summary, then run
`mdsmith fix AGENTS.md`.

<?catalog
glob: "docs/**/*.md"
sort: path
row: "- [{summary}]({filename})"
?>
- [Straw-man architecture for inbox-sorter: module map, core types, the run loop and its per-message atomic Gmail update, the swappable decision engine seam (clef today, Jev or others later), the Gmail seam, on-disk state, dependencies, and open questions.](docs/architecture.md)
- [Non-negotiable engineering rules for inbox-sorter: simplicity and dependencies, code layout, terminal output and exit codes, safety and privacy, and the basic testing bar (fast unit tests, no coverage target).](docs/constitution.md)
- [Product requirements for inbox-sorter v1: goals, non-goals, the label/archive/mark-read triage flow, the setup command that imports Gmail labels, clef questions, when the Sorter label parent is used, config format, safety rules, success metrics, milestones and decisions.](docs/prd.md)
- [Index of inbox-sorter's design documents, with each one's role and whether it binds implementation work or is background only.](docs/README.md)
- [Terminal UX for inbox-sorter: the live run dashboard layout, progress and ETA, resumable runs over a 10k+ unread backlog, stopping safely, keep-awake and notifications, plain log mode, the setup wizard, the trust-building workflow, and the CLI libraries.](docs/ux.md)
<?/catalog?>

<!-- claude-code-stack-profile -->

## Running this stack with Claude Code

- Anything that loads clef (`uv run python smoke_clef.py`, model-backed
  tests, later `inbox-sorter run`) takes about 20 s and 51 GB of memory.
  Run it in the background, log it, then read the tail:
  `uv run python smoke_clef.py > /tmp/inbox-sorter-clef.log 2>&1`. Run
  one model-loading command at a time.
- Quiet the model-loading noise without hiding results:
  `HF_HUB_DISABLE_PROGRESS_BARS=1 TRANSFORMERS_VERBOSITY=error`.
- Run tests targeted and terse: `uv run pytest tests/test_<x>.py -q -x`.
  Re-run only failures with `uv run pytest --lf -q`.
- The first `uv run` after a lock change can download torch. Run it in the
  background and wait. Model weights live in `~/.cache/huggingface`;
  never download them again.
- `uv lock --check` and `mise exec -- mdsmith check .` are fast; run them
  inline.

<!-- /claude-code-stack-profile -->
