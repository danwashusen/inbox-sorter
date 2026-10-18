from dataclasses import replace

import pytest

from inbox_sorter.config import Category, Config, FlagSettings, RunSettings
from inbox_sorter.gmail import LabelInfo, MailboxError
from inbox_sorter.setup import (
    SAMPLE_SIZE,
    SETUP_ACTION_PRESETS,
    SetupCancelled,
    candidate_labels,
    category_id,
    default_min_confidence,
    deleted_label_ids,
    merge,
    run_setup,
)
from tests.factories import ALERTS, RECEIPTS, make_config, make_email
from tests.fake_mailbox import FakeMailbox


def label(name: str, count: int = 3, system: bool = False) -> LabelInfo:
    return LabelInfo(id=f"id-{name}", name=name, system=system, message_count=count)


class ScriptedPrompter:
    """Replays answers in order; a None answer (or running out) takes the default.

    Records every question and preview, and every say line.
    """

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []
        self.previews: list[tuple[LabelInfo, list]] = []
        self.said: list[str] = []

    def preview(self, label, samples):
        self.previews.append((label, list(samples)))

    def confirm(self, question, default):
        return self._next(question, default)

    def text(self, question, default):
        return self._next(question, default)

    def select(self, question, choices, default):
        answer = self._next(question, default)
        assert answer in choices
        return answer

    def say(self, line):
        self.said.append(line)

    def _next(self, question, default):
        self.questions.append(question)
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, BaseException):
            raise answer
        return default if answer is None else answer


class RecordingMailbox(FakeMailbox):
    """Records the name of every Mailbox method called."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[str] = []

    def __getattribute__(self, name):
        attr = super().__getattribute__(name)
        if callable(attr) and not name.startswith("_"):
            super().__getattribute__("calls").append(name)
        return attr


def test_candidate_labels_skip_system_sorter_and_configured_labels():
    labels = [
        label("INBOX", system=True),
        label("CATEGORY_UPDATES"),  # a system name, whatever the flag says
        label("Sorter"),
        label("Sorter/Alerts"),
        label("Sorter/_processed"),
        label("Receipts"),  # configured by RECEIPTS
        label("work"),
        label("Bills"),
        label("Archive/2019"),
    ]

    names = [found.name for found in candidate_labels(labels, make_config(RECEIPTS))]

    assert names == ["Archive/2019", "Bills", "work"]


def test_candidate_labels_skip_labels_of_disabled_categories():
    existing = make_config(replace(RECEIPTS, enabled=False))

    assert candidate_labels([label("Receipts")], existing) == []


def test_deleted_label_ids_disable_only_enabled_imported_categories():
    gone = replace(RECEIPTS, id="bills", gmail_label="Bills")
    already_off = replace(RECEIPTS, id="old", gmail_label="Old", enabled=False)
    existing = make_config(RECEIPTS, gone, already_off, ALERTS)  # Sorter/Alerts is missing too

    assert deleted_label_ids(existing, [label("Receipts")]) == ("bills",)


def test_category_id_slugs_and_deduplicates():
    assert category_id("Work/Clients", set()) == "work-clients"
    assert category_id("Alerts", set()) == "alerts"
    assert category_id("  Café & Bar!  ", set()) == "caf-bar"
    assert category_id("日本", set()) == "category"
    assert category_id("Alerts", {"alerts", "alerts-2"}) == "alerts-3"


def test_default_min_confidence_is_zero_only_for_label_only():
    assert default_min_confidence(("label",)) == 0.0
    for preset in SETUP_ACTION_PRESETS[1:]:
        assert default_min_confidence(preset) == 0.85


def test_merge_without_an_existing_config_uses_the_defaults():
    assert merge(None, (), [RECEIPTS]) == Config(
        run=RunSettings(), flags=FlagSettings(), categories=(RECEIPTS,)
    )


def test_first_run_imports_accepted_labels_and_creates_new_ones():
    mailbox = FakeMailbox(labels=[label("Receipts"), label("Work/Clients"), label("Newsletters")])
    prompter = ScriptedPrompter(
        # Labels come in name order. Newsletters: decline
        False,
        # Receipts: accept, keep the description, label + archive + mark read, 0.9
        True, None, "label, archive, mark read", "0.9",
        # Work/Clients: accept with every default
        True, None, None, None,
        # one new category, typed with the Sorter/ prefix
        True, "  Sorter/Alerts ", "Automated alerts", "label, mark read", None,
        False,
    )  # fmt: skip

    result = run_setup(mailbox, prompter, None)

    assert result.config.run == RunSettings()
    assert result.config.categories == result.added
    assert result.added == (
        Category("receipts", "Receipts", "imported", True, "Receipts",
                 ("label", "archive", "mark_read"), 0.9),
        Category("work-clients", "Work/Clients", "imported", True, "Work/Clients",
                 ("label",), 0.0),
        Category("alerts", "Sorter/Alerts", "created", True, "Automated alerts",
                 ("label", "mark_read"), 0.85),
    )  # fmt: skip
    assert result.disabled == ()
    assert result.changed


def test_enter_everywhere_imports_every_label_as_label_only():
    mailbox = FakeMailbox(labels=[label("Bills"), label("Travel")])

    result = run_setup(mailbox, ScriptedPrompter(), None)

    assert [(c.id, c.source, c.actions, c.min_confidence) for c in result.added] == [
        ("bills", "imported", ("label",), 0.0),
        ("travel", "imported", ("label",), 0.0),
    ]


def test_bad_min_confidence_is_asked_again():
    prompter = ScriptedPrompter(True, None, None, "abc", "1.5", "nan", "0.5", False)

    result = run_setup(FakeMailbox(labels=[label("Bills")]), prompter, None)

    assert result.added[0].min_confidence == 0.5
    assert len(prompter.said) == 3


def test_blank_description_is_asked_again():
    prompter = ScriptedPrompter(True, "   ", "Bills to pay", None, None, False)

    result = run_setup(FakeMailbox(labels=[label("Bills")]), prompter, None)

    assert result.added[0].description == "Bills to pay"
    assert prompter.said == ["the description cannot be blank"]


@pytest.mark.parametrize("bad", ["", "Sorter/", "_x", "Today", "Needs reply", "Alerts", "News"])
def test_unusable_created_names_are_asked_again(bad):
    existing = make_config(RECEIPTS, ALERTS)  # Sorter/Alerts is taken
    # "News" is added first, so a second "News" is taken too.
    prompter = ScriptedPrompter(
        True, "News", None, None, None,
        True, bad, "Fine", None, None, None,
        False,
    )  # fmt: skip

    result = run_setup(FakeMailbox(labels=[label("Receipts")]), prompter, existing)

    assert [c.gmail_label for c in result.added] == ["Sorter/News", "Sorter/Fine"]
    assert len(prompter.said) == 1


def test_rerun_keeps_existing_entries_and_asks_only_about_new_labels():
    existing = Config(
        run=RunSettings(max_messages=9, dry_run=False, body_chars=0),
        flags=FlagSettings(needs_reply_min=0.4, today_min_score=1.0),
        categories=(
            RECEIPTS,
            replace(RECEIPTS, id="bills", gmail_label="Bills"),  # Bills was deleted
            ALERTS,  # created; its label does not exist yet
        ),
    )
    mailbox = FakeMailbox(labels=[label("Receipts"), label("Travel")])
    prompter = ScriptedPrompter(True, None, None, None, False)

    result = run_setup(mailbox, prompter, existing)

    assert result.config.run == existing.run
    assert result.config.flags == existing.flags
    assert result.config.categories[0] == RECEIPTS
    assert result.config.categories[1] == replace(
        existing.categories[1], enabled=False
    )
    assert result.config.categories[2] == ALERTS
    assert [c.gmail_label for c in result.added] == ["Travel"]
    assert result.disabled == ("bills",)
    assert result.unchanged == 2
    assert not any("Receipts" in q or "Bills" in q or "Alerts" in q for q in prompter.questions)


def test_rerun_with_nothing_new_changes_nothing():
    existing = make_config(RECEIPTS, ALERTS)

    result = run_setup(FakeMailbox(labels=[label("Receipts")]), ScriptedPrompter(), existing)

    assert not result.changed
    assert result.config == existing


def test_a_new_imported_id_avoids_existing_ids():
    existing = make_config(replace(RECEIPTS, gmail_label="Old receipts"))  # id "receipts"
    mailbox = FakeMailbox(labels=[label("Old receipts"), label("Receipts")])

    result = run_setup(mailbox, ScriptedPrompter(True, None, None, None, False), existing)

    assert result.added[0].id == "receipts-2"


def test_preview_shows_the_newest_samples_and_skips_a_deleted_message():
    emails = [make_email(f"m{n}", labels=("id-Bills",)) for n in range(SAMPLE_SIZE + 5)]

    class Gone(FakeMailbox):
        def get_message(self, message_id):
            if message_id == "m1":
                raise MailboxError("not found", status=404)
            return super().get_message(message_id)

    prompter = ScriptedPrompter(False, False)
    run_setup(Gone(emails, labels=[label("Bills", count=15)]), prompter, None)

    [(shown, samples)] = prompter.previews
    assert shown.message_count == 15
    assert [s.id for s in samples] == [f"m{n}" for n in range(SAMPLE_SIZE) if n != 1]


def test_other_gmail_errors_end_setup():
    class Broken(FakeMailbox):
        def get_message(self, message_id):
            raise MailboxError("Gmail request failed (500)", status=500)

    mailbox = Broken([make_email("m1", labels=("id-Bills",))], labels=[label("Bills")])

    with pytest.raises(MailboxError):
        run_setup(mailbox, ScriptedPrompter(), None)


def test_setup_only_reads_gmail():
    emails = [make_email("m1", labels=("id-Bills",))]
    mailbox = RecordingMailbox(emails, labels=[label("Bills"), label("Travel")])

    run_setup(mailbox, ScriptedPrompter(True, None, None, None, False), make_config(RECEIPTS))

    assert set(mailbox.calls) <= {"list_labels", "list_by_label", "get_message"}
    assert "list_labels" in mailbox.calls


def test_cancelling_a_prompt_propagates():
    prompter = ScriptedPrompter(SetupCancelled("setup cancelled; nothing was written"))

    with pytest.raises(SetupCancelled):
        run_setup(FakeMailbox(labels=[label("Bills")]), prompter, None)
