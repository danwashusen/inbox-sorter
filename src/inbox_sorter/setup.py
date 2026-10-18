"""The setup wizard: build the category list from existing Gmail labels (prd.md §Setup).

Reads Gmail only (R-setup-read-only) and never writes the config: ``run_setup``
returns the merged config and the CLI writes it. The owner is reached through
a ``Prompter``, which ``ui/prompts.py`` implements (A-setup-prompter).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from inbox_sorter.config import (
    Action,
    Category,
    Config,
    ConfigError,
    FlagSettings,
    RunSettings,
    Source,
    check_category,
)
from inbox_sorter.errors import UserError
from inbox_sorter.gmail import LabelInfo, Mailbox, MailboxError
from inbox_sorter.types import SORTER_PREFIX, SYSTEM_LABELS, Email

SAMPLE_SIZE = 10  # newest messages shown per label (R-setup-list-labels: 5 to 10)

# Every preset applies the label, so no action hides mail without one (prd.md §Safety).
SETUP_ACTION_PRESETS: tuple[tuple[Action, ...], ...] = (
    ("label",),
    ("label", "mark_read"),
    ("label", "archive"),
    ("label", "archive", "mark_read"),
)

# The PRD example's values for a label-only and a hiding category.
LABEL_ONLY_MIN_CONFIDENCE = 0.0
HIDING_MIN_CONFIDENCE = 0.85


class SetupCancelled(UserError):
    """The owner stopped setup, or input ended, before it finished."""


class Prompter(Protocol):
    def preview(self, label: LabelInfo, samples: Sequence[Email]) -> None:
        """Show a label's message count and recent senders and subjects."""
        ...

    def confirm(self, question: str, default: bool) -> bool: ...

    def text(self, question: str, default: str) -> str: ...

    def select(self, question: str, choices: Sequence[str], default: str) -> str: ...

    def say(self, line: str) -> None:
        """Explain why an answer is asked again."""
        ...


@dataclass(frozen=True)
class SetupResult:
    config: Config  # the merged config to write
    added: tuple[Category, ...]
    disabled: tuple[str, ...]  # IDs of imported categories whose label was deleted
    unchanged: int  # existing categories carried over as they were

    @property
    def changed(self) -> bool:
        return bool(self.added or self.disabled)


def candidate_labels(labels: Sequence[LabelInfo], existing: Config | None) -> list[LabelInfo]:
    """User labels no category uses yet, in case-insensitive name order."""
    configured = {category.gmail_label for category in existing.categories} if existing else set()
    return sorted(
        (
            label
            for label in labels
            if not label.system
            and label.name not in SYSTEM_LABELS
            and label.name != SORTER_PREFIX.rstrip("/")
            and not label.name.startswith(SORTER_PREFIX)
            and label.name not in configured
        ),
        key=lambda label: (label.name.casefold(), label.name),
    )


def deleted_label_ids(existing: Config, labels: Sequence[LabelInfo]) -> tuple[str, ...]:
    """Enabled imported categories whose label is gone (R-setup-resync).

    A created category's Sorter/<name> label is made on first use
    (R-create-missing-labels), so its absence means nothing.
    """
    names = {label.name for label in labels}
    return tuple(
        category.id
        for category in existing.categories
        if category.source == "imported"
        and category.enabled
        and category.gmail_label not in names
    )


def category_id(name: str, taken: set[str]) -> str:
    """A slug of ``name`` not in ``taken``: "Work/Clients" gives "work-clients"."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "category"
    candidate, suffix = base, 2
    while candidate in taken:
        candidate, suffix = f"{base}-{suffix}", suffix + 1
    return candidate


def default_min_confidence(actions: tuple[Action, ...]) -> float:
    """0.0 for label only; 0.85 when the actions hide mail."""
    return LABEL_ONLY_MIN_CONFIDENCE if actions == ("label",) else HIDING_MIN_CONFIDENCE


def merge(existing: Config | None, disabled: Sequence[str], added: Sequence[Category]) -> Config:
    """The existing config with ``disabled`` turned off and ``added`` appended."""
    base = existing or Config(run=RunSettings(), flags=FlagSettings(), categories=())
    categories = tuple(
        replace(category, enabled=False) if category.id in disabled else category
        for category in base.categories
    )
    return replace(base, categories=categories + tuple(added))


def run_setup(mailbox: Mailbox, prompter: Prompter, existing: Config | None) -> SetupResult:
    """Walk the new labels, then offer new categories. Reads Gmail only; writes nothing."""
    labels = mailbox.list_labels()
    disabled = deleted_label_ids(existing, labels) if existing else ()
    categories = existing.categories if existing else ()
    taken_ids = {category.id for category in categories}
    taken_labels = {category.gmail_label for category in categories}
    added: list[Category] = []

    for label in candidate_labels(labels, existing):
        prompter.preview(label, _samples(mailbox, label))
        if prompter.confirm(f"Use {label.name} as a category?", True):
            added.append(_ask_category(prompter, label.name, "imported", label.name, taken_ids))
            taken_labels.add(label.name)

    while prompter.confirm("Add a new category?", False):
        name = _ask_new_name(prompter, taken_labels)
        gmail_label = SORTER_PREFIX + name
        added.append(_ask_category(prompter, gmail_label, "created", name, taken_ids))
        taken_labels.add(gmail_label)

    return SetupResult(
        config=merge(existing, disabled, added),
        added=tuple(added),
        disabled=disabled,
        unchanged=len(categories) - len(disabled),
    )


def _samples(mailbox: Mailbox, label: LabelInfo) -> list[Email]:
    """The label's newest messages; one deleted since listing (404) is skipped."""
    samples = []
    for message_id in mailbox.list_by_label(label.name, SAMPLE_SIZE)[:SAMPLE_SIZE]:
        try:
            samples.append(mailbox.get_message(message_id))
        except MailboxError as error:
            if error.status != 404:
                raise
    return samples


def _ask_new_name(prompter: Prompter, taken_labels: set[str]) -> str:
    """A created category's name, without Sorter/; re-asked until it is usable."""
    while True:
        answer = prompter.text(f"Name for the new category (created as {SORTER_PREFIX}<name>)", "")
        name = answer.strip().removeprefix(SORTER_PREFIX).strip()
        if not name:
            prompter.say("enter a name")
            continue
        gmail_label = SORTER_PREFIX + name
        if gmail_label in taken_labels:
            prompter.say(f"{gmail_label} is already a category's label; choose another name")
            continue
        try:
            check_category(_category("new", gmail_label, "created", name, ("label",), 0.0))
        except ConfigError as error:
            prompter.say(f"{error}; choose another name")
            continue
        return name


def _ask_category(
    prompter: Prompter, gmail_label: str, source: Source, name: str, taken_ids: set[str]
) -> Category:
    """Ask the description, actions and min_confidence (R-setup-prompts)."""
    description = ""
    while not description:
        description = prompter.text(f"Description for {gmail_label}", name).strip()
        if not description:
            prompter.say("the description cannot be blank")

    choices = [_preset_name(preset) for preset in SETUP_ACTION_PRESETS]
    choice = prompter.select(f"Actions for {gmail_label}", choices, choices[0])
    actions = SETUP_ACTION_PRESETS[choices.index(choice)]

    min_confidence = _ask_min_confidence(prompter, default_min_confidence(actions))

    category_key = name if source == "created" else gmail_label
    category = _category(
        category_id(category_key, taken_ids), gmail_label, source, description, actions,
        min_confidence,
    )
    check_category(category)
    taken_ids.add(category.id)
    return category


def _ask_min_confidence(prompter: Prompter, default: float) -> float:
    while True:
        answer = prompter.text("Minimum confidence, 0 to 1", repr(default)).strip()
        try:
            value = float(answer)
        except ValueError:
            prompter.say(f"{answer!r} is not a number; enter a number from 0 to 1")
            continue
        if not 0 <= value <= 1:
            prompter.say(f"{answer} is not between 0 and 1")
            continue
        return value


def _preset_name(actions: tuple[Action, ...]) -> str:
    """Such as "label, archive, mark read"."""
    return ", ".join(action.replace("_", " ") for action in actions)


def _category(
    id: str,
    gmail_label: str,
    source: Source,
    description: str,
    actions: tuple[Action, ...],
    min_confidence: float,
) -> Category:
    return Category(
        id=id,
        gmail_label=gmail_label,
        source=source,
        enabled=True,
        description=description,
        actions=actions,
        min_confidence=min_confidence,
    )
