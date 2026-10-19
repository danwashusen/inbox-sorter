---
summary: >-
  Terminal UX for inbox-sorter: the live run dashboard layout, progress and
  ETA, resumable runs over a 10k+ unread backlog, stopping safely, keep-awake
  and notifications, plain log mode, the setup wizard, the trust-building
  workflow, and the CLI libraries.
---
# inbox-sorter UX

Status: draft, v1 scope. Product scope lives in the
[product requirements](prd.md).

## Goals

- Glanceable: one look shows progress, ETA and what the sorter is doing.
- Safe to leave: a backlog of 10,000+ unread emails runs overnight
  unattended.
- Safe to stop: stopping at any point loses no work and leaves no message
  half-handled.
- Pretty: a dashboard worth leaving open, without hiding scrollback.

## Run dashboard

`inbox-sorter run` draws an inline live panel that redraws in place. When
the run ends, the panel is replaced by a summary and the scrollback stays
intact.

```text
╭─ inbox-sorter · APPLY · clef on MPS ──────────────────────╮
│ ━━━━━━━━━━━━━━━━━━━━╺━━━━━━━━━  6,412 / 10,233   62%      │
│ 23.4 msg/min · elapsed 4h 34m · ETA 2h 43m · Sat 03:12     │
├────────────────────────────────────────────────────────────┤
│ Newsletters   ████████████████████  3,201  archived        │
│ Receipts      ██████████            1,544  archived        │
│ Alerts        ██████                  902                  │
│ Personal      ███                     488                  │
│ ⚑ Needs reply 212   ⏰ Today 37   ? Low confidence 277     │
├────────────────────────────────────────────────────────────┤
│ 14:02:11  Amazon.co.uk  Your order has dispa…  Receipts 97% │
│ 14:02:08  Sarah Chen    Dinner on Friday?      Personal ⚑  │
│ 14:02:05  GitHub        Unusual sign-in…       Alerts ⏰   │
╰─ q stop after current · Ctrl-C twice to abort ────────────╯
```

The panel has three parts:

- Header: mode (DRY RUN or APPLY in a contrasting colour), model and device,
  progress bar, count, rate, elapsed time, ETA and finish clock time.
- Categories: one bar per category with its count and the action applied,
  then the flag and low-confidence counters.
- Feed: the last few decisions with time, sender, subject, category,
  confidence and flag icons.

Colours carry meaning only alongside text or icons, so the panel still
reads in a monochrome terminal.

## Long runs

- `run --all` keeps going until no unprocessed inbox mail is left. Without
  it, a run stops at `max_messages`.
- Each message is finished, logged and marked `Sorter/_processed` before
  the next one starts. A crash or stop loses at most the message in flight.
- Rerunning after a stop resumes where it left off. The total and ETA
  count only the remaining mail.
- The rate and ETA use a moving average of recent messages, so they settle
  after a few minutes and follow slowdowns such as body retries.
- The Mac is kept awake for the length of the run and released on exit.
- A macOS notification reports when a run finishes or stops on an error.

## Stopping

- `q` finishes the current message, prints the summary and exits.
- The first Ctrl-C behaves like `q`. A second Ctrl-C aborts at once; the
  message in flight is left unprocessed and retried next run.
- Transient Gmail errors, such as rate limits, retry with backoff and show
  a status line in the panel. A run stops only on errors it cannot retry.

## Summary

The end-of-run summary shows totals per category and action, flag counts,
low-confidence count, errors, elapsed time and the run ID for `undo`. It
names the low-confidence label as the place to review.

## Plain mode

When output is not a terminal, or with `--plain`, the run prints one line
per decision and a periodic progress line instead of the panel. The
format is stable so it can be piped to a file or `grep`.

The shared options go before the command: `inbox-sorter --plain run`.
Each line is a logfmt record: its name, then `key=value` pairs in a fixed
order.

- `started run=<run-id> mode=dry-run engine=<engine> total=<n>`
- `decided id=<message-id> category=<id> confidence=<0.00> reply=<bool>
  today=<bool> low=<bool> body=<bool> add=<labels> remove=<labels>
  sender=<sender> subject=<subject>`, on one line
- `failed id=<message-id> error=<text>`
- `progress done=<n> total=<n>`, after every 10th message except the last
- `finished run=<run-id> decided=<n> failed=<n>`

A value is double-quoted when it is empty or holds a space, `"`, `=`, `\`
or a control character. Inside quotes `\`, `"`, a newline and a tab are
escaped as `\\`, `\"`, `\n` and `\t`, so a record never spans lines. A
carriage return becomes `\r`, and any other control character, line
separator or bidi control `\uXXXX`, in JSON lines too.

`setup` prints its accuracy report as records, after its summary line:

- `progress done=<n> total=<n>`, after every 10th message except the last
- `category id=<id> label=<label> sampled=<n> correct=<n>`, one per
  enabled category
- `confusion actual=<id> predicted=<id> count=<n>`, most common first
- `accuracy engine=<engine> sampled=<n> correct=<n>`, last

Label lists are joined with `,`. `--json` prints the same records as JSON
lines, with the record name in `"event"`, and implies `--plain`. No email
body is ever printed.

## Setup wizard

`setup` walks through labels one at a time. Each step shows the label,
its message count and a short table of up to 10 recent senders and
subjects, cut to the dry-run table's widths. Choices use arrow-key menus
with sensible defaults, so Enter accepts the common answer:

- Use the label as a category: yes.
- Description: the label name. It cannot be blank.
- Actions: label only, picked from label; label and mark read; label and
  archive; or all three.
- `min_confidence`: 0.0 for label only, 0.85 when the actions archive or
  mark read. A value that is not a number from 0 to 1 is asked again.
- Add a new category: no.

Without a terminal, or with `--plain` or `--json`, `setup` asks the same
questions as line prompts on stdin: `[Y/n]` confirms, `[<default>]` text
and numbered choices. Enter takes the default. If input ends early, or on
Ctrl-C during the prompts or sign-in, `setup` exits 1 and writes nothing.
Ctrl-C during the accuracy check exits 1 but keeps the config already
written.

The accuracy report at the end uses the same category bars as the run
dashboard. While messages are decided, a transient progress bar shows
"checking accuracy" with a done/total count. Then the report shows:

- A heading: `Accuracy · <engine> · <correct>/<sampled> correct (<pct>%)`.
- One bar per enabled category, filled by its share of correct guesses,
  with `<correct>/<sampled>  <pct>%` as text, or `no labelled mail`.
- `Most common confusions`: up to 5 lines of
  `<actual label> → <predicted label>  <count>`, left out when there are
  none.

A guess is correct when the message already carries the chosen
category's label. When no enabled category's label has any mail,
`setup` says so and skips the model load.

## Building trust

The intended path to a large `--apply` run:

1. `run --limit 200` (dry run is the default) and read the decisions.
2. Tune category descriptions in the config until the sample looks right.
3. `run --apply --limit 200`, then spot-check Gmail.
4. `run --apply --all` for the backlog, left to run overnight.

## Requirements

Each requirement has a stable slug ID, as in the product requirements.

- R-live-dashboard: In a terminal, `run` shows the inline live panel
  described above and ends with a summary.
- R-progress-eta: The panel shows count, percentage, rate, elapsed time,
  ETA and finish clock time, using a moving-average rate.
- R-decision-feed: The panel shows the most recent decisions with sender,
  subject, category, confidence and flags.
- R-run-all: `run --all` processes all unprocessed inbox mail; without it a
  run stops at `max_messages`.
- R-resumable: A stopped or crashed run resumes from where it left off with
  no message handled twice.
- R-graceful-stop: `q` or one Ctrl-C stops after the current message; two
  Ctrl-C presses abort.
- R-retry-transient: Rate limits and network errors retry with backoff and
  are shown in the panel.
- R-keep-awake: The Mac does not idle-sleep during a run.
- R-notify: A macOS notification reports a finished run or a fatal error.
- R-plain-mode: Without a terminal, or with `--plain`, output is one line
  per decision plus periodic progress lines.
- R-dry-run-limit: `--limit N` caps any run, so a dry run can sample N
  emails.
- R-monochrome-safe: Every colour is paired with text or an icon.
- R-setup-plain-prompts: Without a terminal, or with `--plain` or
  `--json`, `setup` asks the same questions as line prompts on stdin;
  Enter takes the default; input that ends early writes nothing.

## Success metrics

- A 10,000-message backlog finishes in under 10 hours unattended.
- Stopping and resuming a run never reprocesses or skips a message.
- The ETA is within 20% of the actual finish time after the first 10
  minutes.

## Decisions

Settled on 2026-10-18.

- **Display.** An inline live panel, not a full-screen app, so scrollback
  survives (R-live-dashboard).
- **Backlog.** One resumable `run --all` rather than repeated capped runs
  (R-run-all, R-resumable).
- **Review.** Dry run on a sample with `--limit`, then apply; no saved plan
  file in v1 (R-dry-run-limit).
- **Extras.** Keep-awake, notifications and plain mode are in v1. A
  separate `status` command is not.
- **Libraries.** Typer for commands and help, Rich for the live panel and
  summary, and questionary for arrow-key prompts in `setup`. Keep-awake and
  notifications use `caffeinate` and `osascript` as subprocesses, with no
  extra dependency.
- **Plain format.** Logfmt records by default and JSON lines with
  `--json`, with the same record names and keys, settled on 2026-10-18
  (R-plain-mode).
