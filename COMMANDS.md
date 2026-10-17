<!-- github-pipeline-config -->
Pipeline configuration for the `github-pipeline` skills (resolver / evaluator / planner), read at use-time. You can edit these blocks by hand — just keep each block's `<!-- … -->` marker pair intact so the skills can find it. Re-run `github-pipeline-setup` to reconcile them (idempotent).
<!-- /github-pipeline-config -->

<!-- issue-resolver-fast-checks -->
- `uv lock --check` — Lockfile matches pyproject.toml (fast, offline)
- `mise exec -- mdsmith check .` — Strict Markdown lint for all docs
<!-- /issue-resolver-fast-checks -->

<!-- issue-resolver-test-target -->
- wrapper: `uv run pytest`
- targets:
  - `tests` (unit)
    - naming: source `src/inbox_sorter/<x>.py` ↔ `tests/test_<x>.py`; suite id `tests/test_<x>.py`.
    - helpers-fallback: `uv run pytest tests -q`
    - broad-change-fallback: `uv run pytest`
<!-- /issue-resolver-test-target -->

<!-- issue-resolver-canonical-suite -->
- full-suite: `uv run pytest`
- build-once: `uv run pytest`
- retry-without-rebuild: `uv run pytest --lf`
<!-- /issue-resolver-canonical-suite -->

<!-- pr-evaluator-static-checks -->
- `uv lock --check` — Lockfile matches pyproject.toml (fast, offline)
- `mise exec -- mdsmith check .` — Strict Markdown lint for all docs
<!-- /pr-evaluator-static-checks -->

<!-- pr-evaluator-test-target -->
- wrapper: `uv run pytest`
- full-suite-command: `uv run pytest`
- targets:
  - `tests` (unit)
    - naming: source `src/inbox_sorter/<x>.py` ↔ `tests/test_<x>.py`; suite id `tests/test_<x>.py`.
    - helpers-fallback: `uv run pytest tests -q`
    - broad-change-fallback: `uv run pytest`
<!-- /pr-evaluator-test-target -->

<!-- pr-evaluator-merge-policy -->
- standard: auto
- story: ask
<!-- /pr-evaluator-merge-policy -->
