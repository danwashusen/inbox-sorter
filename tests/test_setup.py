from dataclasses import replace

import pytest

from inbox_sorter.config import Category, Config, FlagSettings, RunSettings
from inbox_sorter.engines.fake import FakeEngine
from inbox_sorter.errors import UserError
from inbox_sorter.gmail import LabelInfo, MailboxError
from inbox_sorter.questions import build_questions
from inbox_sorter.setup import (
    ACCURACY_SAMPLE_SIZE,
    CONFUSION_LIMIT,
    SAMPLE_SIZE,
    SETUP_ACTION_PRESETS,
    CategoryAccuracy,
    Confusion,
    SetupCancelled,
    candidate_labels,
    category_id,
    check_accuracy,
    default_min_confidence,
    deleted_label_ids,
    merge,
    run_setup,
    sample_ids,
)
from tests.factories import ALERTS, RECEIPTS, answers, make_config, make_email
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
        run=RunSettings(max_messages=9, body_chars=0),
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


# Accuracy check: Receipts and Sorter/Alerts exist in Gmail as id-<name>.
ACCURACY_LABELS = [label("Receipts"), label("Sorter/Alerts")]


def by_subject(request):
    """Guess alerts for a subject mentioning an alert, else receipts."""
    return answers("alerts" if "alert" in request.state["subject"].lower() else "receipts")


def receipt(id, *labels):
    return make_email(id, labels=labels or ("id-Receipts",), subject=f"Order {id}")


def alert(id, *labels):
    return make_email(id, labels=labels or ("id-Sorter/Alerts",), subject=f"Alert {id}")


def accuracy(mailbox, engine=None, config=None, **kwargs):
    config = config or make_config()
    engine = engine or FakeEngine(by_subject)
    samples = sample_ids(mailbox, config)
    return check_accuracy(mailbox, engine, config, build_questions(config), samples, **kwargs)


def test_every_right_guess_scores_each_category_in_full():
    emails = [receipt(f"r{i}") for i in range(10)] + [alert(f"a{i}") for i in range(10)]

    report = accuracy(FakeMailbox(emails, labels=ACCURACY_LABELS))

    assert report.categories == (
        CategoryAccuracy("receipts", "Receipts", 10, 10),
        CategoryAccuracy("alerts", "Sorter/Alerts", 10, 10),
    )
    assert report.confusions == ()
    assert (report.sampled, report.correct, report.engine) == (20, 20, "fake")


def test_a_wrong_guess_is_a_confusion_from_the_sampled_category():
    # A Receipts-labelled message whose subject reads like an alert.
    emails = [make_email("r1", labels=("id-Receipts",), subject="Alert: card charged")]

    report = accuracy(FakeMailbox(emails, labels=ACCURACY_LABELS))

    assert report.categories[0] == CategoryAccuracy("receipts", "Receipts", 1, 0)
    assert report.confusions == (Confusion("receipts", "alerts", 1),)


def test_a_message_with_both_labels_is_right_either_way_and_decided_once():
    engine = FakeEngine(by_subject)
    emails = [alert("m1", "id-Receipts", "id-Sorter/Alerts")]

    report = accuracy(FakeMailbox(emails, labels=ACCURACY_LABELS), engine)

    assert [(c.sampled, c.correct) for c in report.categories] == [(1, 1), (1, 1)]
    assert report.confusions == ()
    assert len(engine.requests) == 1


def test_only_the_newest_messages_are_sampled():
    emails = [receipt(f"r{i}") for i in range(ACCURACY_SAMPLE_SIZE + 2)]  # newest first
    engine = FakeEngine(by_subject)

    report = accuracy(FakeMailbox(emails, labels=ACCURACY_LABELS), engine)

    assert report.categories[0].sampled == ACCURACY_SAMPLE_SIZE
    assert [r.state["subject"] for r in engine.requests] == [
        f"Order r{i}" for i in range(ACCURACY_SAMPLE_SIZE)
    ]


def test_a_category_without_labelled_mail_is_listed_with_nothing_sampled():
    # Sorter/Alerts does not exist in Gmail yet.
    report = accuracy(FakeMailbox([receipt("r1")], labels=[label("Receipts")]))

    assert report.categories[1] == CategoryAccuracy("alerts", "Sorter/Alerts", 0, 0)


def test_a_guess_whose_label_does_not_exist_yet_is_never_right():
    emails = [make_email("r1", labels=("id-Receipts",), subject="Alert: card charged")]

    report = accuracy(FakeMailbox(emails, labels=[label("Receipts")]))

    assert report.categories[0] == CategoryAccuracy("receipts", "Receipts", 1, 0)
    assert report.confusions == (Confusion("receipts", "alerts", 1),)


def test_a_disabled_category_is_neither_sampled_nor_offered():
    engine = FakeEngine(lambda request: answers("receipts"))
    config = make_config(RECEIPTS, replace(ALERTS, enabled=False))
    emails = [receipt("r1"), alert("a1")]

    report = accuracy(FakeMailbox(emails, labels=ACCURACY_LABELS), engine, config)

    assert [c.category for c in report.categories] == ["receipts"]
    assert [r.state["subject"] for r in engine.requests] == ["Order r1"]
    assert set(engine.requests[0].questions["category"].options) == {"receipts"}


class Deleting(FakeMailbox):
    """Fails get_message, or get_body, for one ID with a given status."""

    def __init__(self, *args, missing="", status=404, body=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.missing, self.status, self.body = missing, status, body

    def get_message(self, message_id):
        if message_id == self.missing and not self.body:
            raise MailboxError("gone", status=self.status)
        return super().get_message(message_id)

    def get_body(self, message_id, max_chars):
        if message_id == self.missing:
            raise MailboxError("gone", status=self.status)
        return super().get_body(message_id, max_chars)


def test_a_message_deleted_since_listing_is_skipped():
    mailbox = Deleting([receipt("r1"), receipt("r2")], labels=ACCURACY_LABELS, missing="r1")

    report = accuracy(mailbox)

    assert report.categories[0] == CategoryAccuracy("receipts", "Receipts", 1, 1)


def test_a_message_whose_body_is_gone_is_skipped():
    def unsure(request):
        return answers(confidence=0.97 if "body" in request.state else 0.5)

    mailbox = Deleting(
        [receipt("r1"), receipt("r2")], {"r2": "Receipt"}, ACCURACY_LABELS, missing="r1", body=True
    )

    report = accuracy(mailbox, FakeEngine(unsure))

    assert report.categories[0] == CategoryAccuracy("receipts", "Receipts", 1, 1)


def test_other_gmail_errors_end_the_check():
    mailbox = Deleting([receipt("r1")], labels=ACCURACY_LABELS, missing="r1", status=500)

    with pytest.raises(MailboxError):
        accuracy(mailbox)


def test_sample_ids_lists_each_enabled_category_in_config_order():
    config = make_config(ALERTS, RECEIPTS, replace(RECEIPTS, id="old", enabled=False))
    mailbox = RecordingMailbox([receipt("r1"), receipt("r2")], labels=[label("Receipts")])

    assert sample_ids(mailbox, config) == {"alerts": [], "receipts": ["r1", "r2"]}
    assert set(mailbox.calls) == {"list_by_label"}


def test_the_check_only_reads_gmail():
    emails = [receipt("r1"), make_email("a1", labels=("id-Sorter/Alerts",), subject="Alert")]
    mailbox = RecordingMailbox(emails, {"a1": "body"}, ACCURACY_LABELS)

    accuracy(mailbox, FakeEngine(lambda request: answers(confidence=0.5)))

    assert set(mailbox.calls) <= {"list_labels", "list_by_label", "get_message", "get_body"}
    assert "get_body" in mailbox.calls


def test_a_low_confidence_guess_is_retried_with_the_body_and_scored_on_the_retry():
    # Receipts at 0.5 is below its 0.85, so the body is fetched; the retry says alerts.
    def unsure_then_alert(request):
        if "body" in request.state:
            return answers("alerts", confidence=0.97)
        return answers("receipts", confidence=0.5)

    engine = FakeEngine(unsure_then_alert)
    mailbox = FakeMailbox([receipt("r1")], {"r1": "Your card alert"}, ACCURACY_LABELS)

    report = accuracy(mailbox, engine)

    assert engine.requests[1].state["body"] == "Your card alert"
    assert report.categories[0] == CategoryAccuracy("receipts", "Receipts", 1, 0)
    assert report.confusions == (Confusion("receipts", "alerts", 1),)


def test_a_non_local_engine_is_refused_before_any_gmail_call():
    class Remote(FakeEngine):
        runs_locally = False

    mailbox = RecordingMailbox([receipt("r1")], labels=ACCURACY_LABELS)
    config = make_config()

    with pytest.raises(UserError, match="R-local-only"):
        check_accuracy(mailbox, Remote(), config, build_questions(config), {"receipts": ["r1"]})
    assert mailbox.calls == []


def test_progress_counts_distinct_messages():
    calls = []
    emails = [receipt("r1"), alert("a1"), alert("both", "id-Receipts", "id-Sorter/Alerts")]

    accuracy(FakeMailbox(emails, labels=ACCURACY_LABELS), progress=lambda *c: calls.append(c))

    assert calls == [(1, 3), (2, 3), (3, 3)]


def test_confusions_are_ranked_and_cut_to_the_limit():
    categories = [
        replace(RECEIPTS, id=f"c{i}", gmail_label=f"L{i}", min_confidence=0.0) for i in range(4)
    ]
    labels = [label(f"L{i}") for i in range(4)]
    # c0 mail: two guessed c2, one c1. c1 mail: one each of c0, c2, c3. c3 mail: one c0.
    guesses = {"a": "c2", "b": "c2", "c": "c1", "d": "c0", "e": "c2", "f": "c3", "g": "c0"}
    sampled = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1, "f": 1, "g": 3}
    emails = [make_email(id, labels=(f"id-L{n}",), subject=id) for id, n in sampled.items()]
    engine = FakeEngine(lambda request: answers(guesses[request.state["subject"]]))

    report = accuracy(FakeMailbox(emails, labels=labels), engine, make_config(*categories))

    assert CONFUSION_LIMIT == 5
    assert report.confusions == (
        Confusion("c0", "c2", 2),
        Confusion("c0", "c1", 1),
        Confusion("c1", "c0", 1),
        Confusion("c1", "c2", 1),
        Confusion("c1", "c3", 1),
    )
