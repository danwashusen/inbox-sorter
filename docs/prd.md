---
summary: >-
  Product requirements for inbox-sorter v1: goals, non-goals, the
  label/archive/mark-read triage flow, the setup command that imports
  Gmail labels, clef questions, when the Sorter label parent is used,
  config format, safety rules, success metrics, milestones and decisions.
---
# inbox-sorter PRD

Status: draft, v1 scope. Owner: Dan. Terminal UX, progress display and
long runs are in the [terminal UX spec](ux.md).

## Problem

My Gmail inbox mixes personal mail with newsletters, receipts and service
alerts. Sorting it by hand takes time, and Gmail filters only match fixed
patterns. I want local, private triage that understands what each email is.

## Goals

- Label every new inbox email with one category from a list I control.
- Bootstrap that list from the labels I already use in Gmail.
- Flag mail that needs a reply or attention today.
- Archive and mark read the categories I never need to see in the inbox.
- Run entirely on my Mac: email content never leaves the machine.
- Make every change visible, logged and reversible.

## Non-goals (v1)

- Deleting, trashing or marking mail as spam.
- Drafting or sending replies.
- Scheduled or always-on runs. v1 is a manual CLI.
- Multiple accounts, or providers other than Gmail.
- A GUI.

## How it works

The CLI has three commands: `setup` builds the config, `run` sorts mail,
and `undo` reverses an applied run.

One run of `inbox-sorter run` does the following:

1. Load the config and the [clef](https://huggingface.co/Cloudflare/clef)
   model on the local GPU (MPS).
2. Fetch inbox messages that lack the `Sorter/_processed` label, up to a
   per-run cap, or all of them with `--all`.
3. For each message, send sender, subject and snippet to clef as the state.
   Ask the three questions below in one call.
4. If category confidence is below its threshold, fetch the plain-text body
   and ask again with it. Use the second answer.
5. With `--apply`, append the decision to `runs.jsonl`. A dry run stops
   here and changes nothing.
6. Apply the actions, the flag labels and `Sorter/_processed` in one
   message change.

Clef returns a probability per option in one forward pass. There is no
free-text output to parse.

## Clef questions

| ID            | Type     | Options                     | Used for                   |
| ------------- | -------- | --------------------------- | -------------------------- |
| `category`    | `choice` | Configured categories       | Category label and actions |
| `needs_reply` | `noul`   | Probability that it is true | `Sorter/Needs reply` flag  |
| `urgency`     | `score`  | Can wait, This week, Today  | `Sorter/Today` flag        |

## Gmail labels

The `Sorter` parent holds only labels the tool owns. A category imported
from an existing Gmail label applies that label as is, so sorted mail sits
with mail I filed by hand. Labels starting with `_` are bookkeeping markers.

| Label                           | When it is used                                       |
| ------------------------------- | ----------------------------------------------------- |
| Existing label, e.g. `Receipts` | Category imported from Gmail by `setup`               |
| `Sorter/<name>`                 | Category created new in `setup`, e.g. `Sorter/Alerts` |
| `Sorter/Needs reply`            | `needs_reply` at or above its threshold               |
| `Sorter/Today`                  | `urgency` score at or above its threshold             |
| `Sorter/_low-confidence`        | Category still below threshold after the body         |
| `Sorter/_processed`             | Handled by a run; skipped next time                   |

## Setup

`inbox-sorter setup` builds the category list from my Gmail labels and
writes `inbox-sorter.toml`. It is the only command that edits the config.

1. Fetch all user labels. Skip system labels (`INBOX`, `SENT`, `CATEGORY_*`
   and so on) and everything under `Sorter`.
2. For each label, show its message count and 5 to 10 recent senders and
   subjects. Ask whether to use it as a category.
3. For each accepted label, prompt for the description, actions and
   `min_confidence`. The description is prefilled with the label name.
4. Offer to add new categories, created as `Sorter/<name>`.
5. Write the config and print a summary.
6. Classify a sample of mail that already carries each enabled
   category's label. Report per-category accuracy and the most common
   confusions.

The accuracy check runs whenever the config has an enabled category,
even on a re-sync that changes nothing. So a description edited by hand
can be checked by running the command again. It runs after the write, so
a failed model load or a stopped check never loses the answers.

A label declined in step 2 is offered again the next time `setup` runs.

Re-running `setup` keeps existing entries' values untouched. It walks
through new Gmail labels only, and sets `enabled = false` on imported
categories whose label was deleted. A created category's label is made
on first use (R-create-missing-labels), so a missing one disables
nothing. A re-sync that changes anything rewrites the file
without its comments.

## Configuration

Categories live in `inbox-sorter.toml`. The description is the text clef
classifies against, so tuning it is the main way to fix wrong answers.

`setup` and `run` read `inbox-sorter.toml` from the current directory. Pass
`--config PATH` to either command to read another file instead.

```toml
[run]
max_messages = 50
body_chars = 2000 # body text sent on the low-confidence retry; 0 turns the retry off

[flags]
needs_reply_min = 0.7 # probability
today_min_score = 1.5 # expected urgency score, 0 to 2

[[category]]
id = "receipts"
gmail_label = "Receipts" # imported: existing label, applied as is
source = "imported"
enabled = true
description = "Order confirmation, shipping update, invoice or receipt"
actions = ["label", "archive", "mark_read"]
min_confidence = 0.85

[[category]]
id = "alerts"
gmail_label = "Sorter/Alerts" # created new in setup
source = "created"
enabled = true
description = "Automated alert or notice from a service"
actions = ["label"]
min_confidence = 0.0
```

Only `run --apply` changes the mailbox; no config key does. Disabled
categories are not offered to clef. An omitted `[run]` or
`[flags]` table, or an omitted key in one, takes the value shown above;
every `[[category]]` key is required.

`setup` rewrites the whole file from its values, so comments are not
kept. When nothing changes it leaves the file as is.

## Requirements

Each requirement has a stable slug ID. Reference it as plain text, e.g.
R-dry-run-default.

- R-classify-one-category: Classify each message into exactly one configured
  category with clef.
- R-actions: Supported actions: `label`, `archive`, `mark_read`.
- R-low-confidence-label-only: Below `min_confidence` after the body retry,
  apply only the label, plus `Sorter/_low-confidence`.
- R-create-missing-labels: Create missing Gmail labels on first use.
- R-skip-processed: Skip messages that already carry `Sorter/_processed`.
- R-dry-run-default: Dry run is the default and prints a table of planned
  actions.
- R-apply-flag: `--apply` performs the actions through the Gmail API.
- R-run-log: Append each applied decision to `runs.jsonl` in the state
  directory: run ID, message ID, probabilities, labels added and removed.
- R-undo: `inbox-sorter undo <run-id>` reverses the actions of one applied run.
- R-oauth-modify-scope: OAuth uses the `gmail.modify` scope; tokens stay in a
  local file.
- R-local-only: Email content is never sent to a remote service other than
  Gmail.
- R-body-retry: Classify from the snippet first; retry once with up to
  `body_chars` of plain-text body when below `min_confidence`.
- R-single-clef-call: Ask `category`, `needs_reply` and `urgency` in the same
  clef call.
- R-flag-labels: Apply `Sorter/Needs reply` and `Sorter/Today` from the
  `[flags]` thresholds.
- R-flagged-stay-in-inbox: Never archive or mark read a message that gets either
  flag.
- R-label-placement: Apply imported categories' existing labels as is; create
  all other labels under the `Sorter` parent.
- R-setup-list-labels: `setup` lists user labels, skipping system labels and
  `Sorter/*`, with counts and sample senders and subjects.
- R-setup-prompts: `setup` prompts per accepted label for description, actions
  and `min_confidence`, then writes the config.
- R-setup-accuracy: after writing the config, `setup` reports
  per-category accuracy on a sample of already-labelled mail, up to 10
  messages per enabled category.
- R-setup-resync: Re-running `setup` never changes existing entries' values;
  it offers new labels and disables imported categories whose label was
  deleted.
- R-setup-read-only: `setup` only reads Gmail; it never changes labels or
  messages.

## Safety

Archive and mark-read hide mail, so a wrong guess can bury something
important. Four rules limit the damage:

- Dry run by default (R-dry-run-default). Changing Gmail always takes an
  explicit flag.
- Low-confidence messages stay in the inbox, labelled for review
  (R-low-confidence-label-only).
- Flagged messages always stay in the inbox and unread
  (R-flagged-stay-in-inbox).
- Every applied run can be undone from the log (R-run-log, R-undo).

The `gmail.modify` scope cannot permanently delete mail.

## Success metrics

- At least 90% of categories match my judgement on a hand-checked sample of
  100 emails.
- At least 90% of mail I would answer gets `Sorter/Needs reply`.
- No personal email archived in the first month of use.
- A 50-message run finishes in under 5 minutes, model load included.

## Milestones

1. Clef smoke test on MPS. Done: about 2.6 s per email after an 18 s load.
2. Read-only Gmail fetch into a local JSON sample.
3. `setup`: import labels, write the config, report accuracy.
4. Dry-run classification over the sample, then tune category descriptions.
5. `--apply` with labels, flags, archive, mark read and the run log.
6. Live dashboard, `--all` and long-run support from the UX spec.
7. `undo`, and a hand-checked accuracy review against the metrics.

## Decisions

Settled on 2026-10-18.

- **Email content.** Snippet first, then plain-text body only when the category
  is below its threshold (R-body-retry). Most mail stays fast; hard cases get
  more context.
- **Questions.** Category, `needs_reply` and `urgency` in v1
  (R-single-clef-call). They share one forward pass, so the extra questions cost
  almost nothing.
- **Label layout.** Imported categories use the existing Gmail label.
  `Sorter` holds only tool-owned labels: flags, markers and new categories
  (R-label-placement). Label colours are out of scope for v1.
- **Setup command.** Categories are bootstrapped from existing Gmail
  labels by `inbox-sorter setup` (the R-setup-* requirements).
- **Setup descriptions.** `setup` shows sample senders and subjects for each
  label to help write its description (R-setup-list-labels, R-setup-prompts).
- **Setup accuracy check.** `setup` reports accuracy on already-labelled
  mail, so weak descriptions are fixed before any `--apply` (R-setup-accuracy).
- **Setup re-sync.** Re-running `setup` offers new labels only and disables
  categories whose label was deleted (R-setup-resync).
