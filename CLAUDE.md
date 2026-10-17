# inbox-sorter

A local Python CLI that triages a Gmail inbox with the Cloudflare clef
decision model running on Apple Silicon (MPS).

## Commands

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
`mdsmith fix CLAUDE.md`.

<?catalog
glob: "docs/**/*.md"
sort: path
row: "- [{summary}]({filename})"
?>
- [Product requirements for inbox-sorter v1: goals, non-goals, the label/archive/mark-read triage flow, the setup command that imports Gmail labels, clef questions, when the Sorter label parent is used, config format, safety rules, success metrics, milestones and decisions.](docs/prd.md)
<?/catalog?>
