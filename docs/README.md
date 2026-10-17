---
summary: >-
  Index of inbox-sorter's design documents, with each one's role and
  whether it binds implementation work or is background only.
---
# inbox-sorter docs

These documents ground planning and implementation. A binding document
must not be contradicted: raise a conflict instead of designing around it.

- [Constitution](constitution.md): role constitution, binding.
  Engineering rules: simplicity, layout, terminal output, safety, privacy
  and the basic testing bar.
- [Architecture](architecture.md): role architecture, informative while
  a straw-man. Module map, run loop, Gmail and decision-engine seams,
  storage.
- [Product requirements](prd.md): role prd, binding. v1 scope, triage
  flow, setup command, labels, config, safety and R-slug requirements.
- [Terminal UX](ux.md): role ui-design, binding. Live dashboard, long
  resumable runs, stopping, plain mode and the setup wizard.

<!-- doc-catalogue -->
- `docs/constitution.md` — constitution — binding — Engineering rules: simplicity, layout, terminal output, safety, privacy and the basic testing bar.
- `docs/architecture.md` — architecture — informative — Straw-man architecture: module map, run loop, Gmail and swappable decision-engine seams, storage, open questions.
- `docs/prd.md` — prd — binding — Product requirements: v1 scope, triage flow, setup command, labels, config, safety, and R-slug requirements.
- `docs/ux.md` — ui-design — binding — Terminal UX: live dashboard, long resumable runs, stopping, plain mode, and the setup wizard.
<!-- /doc-catalogue -->
