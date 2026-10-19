import json
import os

import pytest

from typer.testing import CliRunner

from inbox_sorter import cli
from inbox_sorter.config import load_config
from inbox_sorter.engines.fake import FakeEngine
from inbox_sorter.gmail import LabelInfo, MailboxError
from tests.factories import answers, make_email
from tests.fake_mailbox import FakeMailbox

CONFIG = """
[[category]]
id = "receipts"
gmail_label = "Receipts"
source = "imported"
enabled = true
description = "Order confirmation, shipping update, invoice or receipt"
actions = ["label", "archive", "mark_read"]
min_confidence = 0.85
"""


def test_plain_stdout_holds_only_records_even_when_sign_in_prints(tmp_path, monkeypatch):
    def connect(state_dir):
        print("Please visit this URL to authorize this application: https://example")
        return FakeMailbox([make_email("m1"), make_email("m2")])

    engine = FakeEngine(lambda request: answers())
    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "make_engine", lambda kind: engine)
    config = tmp_path / "inbox-sorter.toml"
    config.write_text(CONFIG)

    result = CliRunner().invoke(cli.app, ["--json", "run", "--config", str(config)])

    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["event"] for r in records] == [
        "started", "decided", "decided", "finished", "total", "summary",
    ]  # fmt: skip
    assert records[-1]["mode"] == "dry-run"
    assert "Please visit this URL" in result.stderr
    assert "loading the clef model" in result.stderr
    assert not (tmp_path / "runs.jsonl").exists()


def apply_args(tmp_path, monkeypatch, mailbox):
    """Fake sign-in and the model load, write CONFIG, and return the apply run's arguments."""
    config = setup_cli(tmp_path, monkeypatch, mailbox, FakeEngine(lambda request: answers()))
    config.write_text(CONFIG)
    return ["--json", "run", "--apply", "--config", str(config)]


def apply_cli(tmp_path, monkeypatch, mailbox):
    return CliRunner().invoke(cli.app, apply_args(tmp_path, monkeypatch, mailbox))


def test_apply_logs_each_decision_and_changes_gmail(tmp_path, monkeypatch):
    mailbox = FakeMailbox([make_email("m1"), make_email("m2")])

    result = apply_cli(tmp_path, monkeypatch, mailbox)

    assert result.exit_code == 0, result.output
    lines = (tmp_path / "runs.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["m1", "m2"]
    assert len(mailbox.modify_calls) == 2
    summary = json.loads(result.stdout.splitlines()[-1])
    assert (summary["event"], summary["mode"], summary["decided"]) == ("summary", "apply", 2)


def test_main_prints_the_summary_then_the_error_and_exits_1(tmp_path, monkeypatch, capsys):
    mailbox = FakeMailbox([make_email("m1"), make_email("m2")])
    mailbox.fail_modify = {"m2"}
    argv = ["inbox-sorter", *apply_args(tmp_path, monkeypatch, mailbox)]
    monkeypatch.setattr(cli.sys, "argv", argv)

    with pytest.raises(SystemExit) as exited:
        cli.main()

    out, err = capsys.readouterr()
    assert exited.value.code == 1
    assert json.loads(out.splitlines()[-1])["event"] == "summary"
    assert "error: modify failed for 'm2'" in err


def test_an_error_before_the_run_starts_prints_no_summary(tmp_path, monkeypatch):
    class Unlistable(FakeMailbox):
        def list_unprocessed(self, page_token=None, page_size=100):
            raise MailboxError("Gmail request failed (500): boom", status=500)

    result = apply_cli(tmp_path, monkeypatch, Unlistable([make_email("m1")]))

    assert result.exit_code == 1
    assert str(result.exception) == "Gmail request failed (500): boom"
    assert result.stdout == ""


def test_a_fatal_error_mid_apply_still_prints_the_summary(tmp_path, monkeypatch):
    mailbox = FakeMailbox([make_email("m1"), make_email("m2")])
    mailbox.fail_modify = {"m2"}

    result = apply_cli(tmp_path, monkeypatch, mailbox)

    assert result.exit_code == 1
    assert isinstance(result.exception, MailboxError)
    events = [json.loads(line)["event"] for line in result.stdout.splitlines()]
    assert events == ["started", "decided", "total", "summary"]


def test_no_enabled_category_names_the_config_file(tmp_path):
    config = tmp_path / "inbox-sorter.toml"
    config.write_text(CONFIG.replace("enabled = true", "enabled = false"))

    result = CliRunner().invoke(cli.app, ["run", "--config", str(config)])

    assert result.exit_code == 1
    assert str(result.exception).startswith(f"{config}: no category is enabled")


def setup_cli(tmp_path, monkeypatch, mailbox, engine=None):
    """Fake sign-in and the model load; ``engine.loads`` counts make_engine calls."""
    engine = engine or FakeEngine()  # uniform answers always pick an offered category
    engine.loads = 0

    def connect(state_dir):
        print("Please visit this URL to authorize this application: https://example")
        return mailbox

    def load(kind):
        print("model weights loaded")  # loading prints; it belongs on stderr
        engine.loads += 1
        return engine

    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "make_engine", load)
    return tmp_path / "inbox-sorter.toml"


def labels(*names):
    return [LabelInfo(id=f"id-{n}", name=n, system=False, message_count=1) for n in names]


def test_setup_writes_a_config_that_run_reads(tmp_path, monkeypatch):
    mailbox = FakeMailbox([make_email("m1", labels=("id-Bills",))], labels=labels("Bills"))
    config = setup_cli(tmp_path, monkeypatch, mailbox)
    # Accept Bills with every default, then decline a new category.
    answers_in = "\n\n\n\nn\n"

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input=answers_in)

    assert result.exit_code == 0, result.output
    [category] = load_config(config).categories
    assert (category.id, category.gmail_label, category.source) == ("bills", "Bills", "imported")
    assert "Bills · 1 messages" in result.stdout
    assert f"wrote {config}: 1 added, 0 disabled, 0 unchanged" in result.stdout
    assert "+ bills → Bills" in result.stdout
    assert "Please visit this URL" in result.stderr
    assert "Please visit this URL" not in result.stdout


def test_setup_with_nothing_new_leaves_the_file_byte_for_byte(tmp_path, monkeypatch):
    config = setup_cli(tmp_path, monkeypatch, FakeMailbox(labels=labels("Receipts")))
    config.write_text(CONFIG)

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input="n\n")

    assert result.exit_code == 0, result.output
    assert config.read_text() == CONFIG
    assert f"{config} is up to date; nothing was written" in result.stdout


def test_setup_disables_a_deleted_imported_label(tmp_path, monkeypatch):
    config = setup_cli(tmp_path, monkeypatch, FakeMailbox(labels=labels()))
    config.write_text(CONFIG)

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input="n\n")

    assert result.exit_code == 0, result.output
    assert not load_config(config).categories[0].enabled
    assert "- receipts (label Receipts was deleted)" in result.stdout
    assert "note: no category is enabled" in result.stdout


def test_setup_with_nothing_accepted_writes_nothing(tmp_path, monkeypatch):
    config = setup_cli(tmp_path, monkeypatch, FakeMailbox(labels=labels("Bills")))

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input="n\nn\n")

    assert result.exit_code == 0, result.output
    assert not config.exists()
    assert "no categories accepted; nothing was written" in result.stdout


def test_setup_input_ending_early_exits_1_and_writes_nothing(tmp_path, monkeypatch):
    config = setup_cli(tmp_path, monkeypatch, FakeMailbox(labels=labels("Bills")))

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input="y\n")

    assert result.exit_code == 1
    assert str(result.exception) == "input ended before setup finished; nothing was written"
    assert not config.exists()


def test_setup_stops_on_an_invalid_config_before_sign_in(tmp_path, monkeypatch):
    def connect(state_dir):
        raise AssertionError("signed in")

    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    config = tmp_path / "inbox-sorter.toml"
    config.write_text("[run\n")

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)])

    assert result.exit_code == 1
    assert "invalid TOML" in str(result.exception)


def test_setup_stops_before_sign_in_when_the_directory_is_missing(tmp_path, monkeypatch):
    def connect(state_dir):
        raise AssertionError("signed in")

    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    config = tmp_path / "missing" / "inbox-sorter.toml"

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)])

    assert result.exit_code == 1
    assert "does not exist" in str(result.exception)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to any directory")
def test_setup_stops_before_sign_in_when_the_directory_is_read_only(tmp_path, monkeypatch):
    def connect(state_dir):
        raise AssertionError("signed in")

    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = CliRunner().invoke(cli.app, ["setup", "--config", str(locked / "a.toml")])
    finally:
        locked.chmod(0o700)

    assert result.exit_code == 1
    assert "cannot write to the directory" in str(result.exception)


def test_ctrl_c_while_fetching_samples_cancels_setup(tmp_path, monkeypatch):
    class Interrupted(FakeMailbox):
        def list_by_label(self, label, limit):
            raise KeyboardInterrupt

    config = setup_cli(tmp_path, monkeypatch, Interrupted(labels=labels("Bills")))

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)])

    assert result.exit_code == 1
    assert str(result.exception) == "setup cancelled; nothing was written"
    assert not config.exists()


def test_setup_checks_the_directory_a_symlinked_config_points_into(tmp_path, monkeypatch):
    def connect(state_dir):
        raise AssertionError("signed in")

    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    link = tmp_path / "inbox-sorter.toml"
    link.symlink_to(tmp_path / "unmounted" / "inbox-sorter.toml")

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(link)])

    assert result.exit_code == 1
    assert "unmounted does not exist" in str(result.exception)


def test_ctrl_c_during_sign_in_cancels_setup(tmp_path, monkeypatch):
    def connect(state_dir):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    config = tmp_path / "inbox-sorter.toml"

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)])

    assert result.exit_code == 1
    assert str(result.exception) == "setup cancelled; nothing was written"


ACCEPT_BILLS = "\n\n\n\nn\n"  # accept Bills with every default, then no new category


def test_setup_checks_accuracy_after_writing_the_config(tmp_path, monkeypatch):
    mailbox = FakeMailbox([make_email("m1", labels=("id-Bills",))], labels=labels("Bills"))
    config = setup_cli(tmp_path, monkeypatch, mailbox)

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input=ACCEPT_BILLS)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    # The last prompt has no newline of its own, so the line may start with it.
    summary = f"wrote {config}: 1 added, 0 disabled, 0 unchanged"
    [wrote] = [i for i, line in enumerate(lines) if line.endswith(summary)]
    assert lines[wrote + 2 :] == [
        "category id=bills label=Bills sampled=1 correct=1",
        "accuracy engine=fake sampled=1 correct=1",
    ]
    assert "loading the clef model" in result.stderr
    assert "model weights loaded" in result.stderr


def test_a_resync_that_changes_nothing_still_checks_accuracy(tmp_path, monkeypatch):
    mailbox = FakeMailbox([make_email("m1", labels=("id-Receipts",))], labels=labels("Receipts"))
    config = setup_cli(tmp_path, monkeypatch, mailbox)
    config.write_text(CONFIG)

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input="n\n")

    assert result.exit_code == 0, result.output
    assert config.read_text() == CONFIG
    assert result.stdout.splitlines()[-1] == "accuracy engine=fake sampled=1 correct=1"


def test_json_setup_prints_the_accuracy_records_as_json(tmp_path, monkeypatch):
    mailbox = FakeMailbox([make_email("m1", labels=("id-Receipts",))], labels=labels("Receipts"))
    config = setup_cli(tmp_path, monkeypatch, mailbox)
    config.write_text(CONFIG)

    args = ["--json", "setup", "--config", str(config)]
    result = CliRunner().invoke(cli.app, args, input="n\n")

    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.stdout.splitlines()[-2:]]
    assert [r["event"] for r in records] == ["category", "accuracy"]


def test_no_accepted_category_skips_the_model_load(tmp_path, monkeypatch):
    engine = FakeEngine()
    config = setup_cli(tmp_path, monkeypatch, FakeMailbox(labels=labels("Bills")), engine)

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input="n\nn\n")

    assert result.exit_code == 0, result.output
    assert engine.loads == 0


def test_no_labelled_mail_skips_the_model_load(tmp_path, monkeypatch):
    engine = FakeEngine()
    config = setup_cli(tmp_path, monkeypatch, FakeMailbox(labels=labels("Receipts")), engine)
    config.write_text(CONFIG)

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input="n\n")

    assert result.exit_code == 0, result.output
    assert engine.loads == 0
    assert result.stdout.splitlines()[-1] == "no labelled mail to check accuracy against"


def test_ctrl_c_during_the_check_keeps_the_written_config(tmp_path, monkeypatch):
    def interrupt(request):
        raise KeyboardInterrupt

    engine = FakeEngine(interrupt)
    closed = []
    engine.close = lambda: closed.append(True)
    mailbox = FakeMailbox([make_email("m1", labels=("id-Bills",))], labels=labels("Bills"))
    config = setup_cli(tmp_path, monkeypatch, mailbox, engine)

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input=ACCEPT_BILLS)

    assert result.exit_code == 1
    assert config.exists()
    assert "nothing was written" not in str(result.exception)
    assert str(result.exception).startswith("accuracy check stopped")
    assert closed == [True]


def test_a_failed_model_load_keeps_the_written_config(tmp_path, monkeypatch):
    mailbox = FakeMailbox([make_email("m1", labels=("id-Bills",))], labels=labels("Bills"))
    config = setup_cli(tmp_path, monkeypatch, mailbox)

    def broken(kind):
        raise OSError("out of memory")

    monkeypatch.setattr(cli, "make_engine", broken)

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input=ACCEPT_BILLS)

    assert result.exit_code == 1
    assert str(result.exception).startswith("could not load the clef model")
    assert str(result.exception).endswith("the config on disk is as reported above")
    assert load_config(config).categories[0].id == "bills"


def test_a_gmail_error_during_the_check_says_the_config_was_kept(tmp_path, monkeypatch):
    class Failing(FakeMailbox):
        """Lists labels for the wizard, then fails the check's own listing."""

        calls = 0

        def list_labels(self):
            self.calls += 1
            if self.calls > 1:
                raise MailboxError("Gmail request failed (500)", status=500)
            return super().list_labels()

    mailbox = Failing([make_email("m1", labels=("id-Bills",))], labels=labels("Bills"))
    config = setup_cli(tmp_path, monkeypatch, mailbox)

    result = CliRunner().invoke(cli.app, ["setup", "--config", str(config)], input=ACCEPT_BILLS)

    assert result.exit_code == 1
    assert str(result.exception) == (
        "Gmail request failed (500); the config on disk is as reported above"
    )
    assert config.exists()


# Undo


def no_sign_in(tmp_path, monkeypatch):
    """Point the state directory at ``tmp_path`` and fail the test on any sign-in."""

    def connect(state_dir):
        pytest.fail("undo signed in")

    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)


def applied_run(tmp_path, monkeypatch):
    """Apply a run over two messages; return the mailbox, their labels before, and the run ID."""
    mailbox = FakeMailbox([make_email("m1"), make_email("m2")], labels=labels("Receipts"))
    before = [email.label_ids for email in mailbox.emails]
    assert apply_cli(tmp_path, monkeypatch, mailbox).exit_code == 0
    run_id = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[0])["run"]
    return mailbox, before, run_id


@pytest.mark.parametrize("args", [["undo", "nope"], ["undo", "nope", "--apply"]])
def test_undo_of_an_unknown_run_exits_1_with_one_line_before_sign_in(
    tmp_path, monkeypatch, capsys, args
):
    applied_run(tmp_path, monkeypatch)
    no_sign_in(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.sys, "argv", ["inbox-sorter", *args])

    with pytest.raises(SystemExit) as exited:
        cli.main()

    out, err = capsys.readouterr()
    assert exited.value.code == 1 and out == ""
    assert err.splitlines() == [
        "error: no applied run nope in the run log; dry runs are not logged, "
        "and an apply run prints its ID in its summary"
    ]


def test_undo_with_no_log_or_a_bad_line_stops_before_sign_in(tmp_path, monkeypatch):
    no_sign_in(tmp_path, monkeypatch)

    missing = CliRunner().invoke(cli.app, ["undo", "r1", "--apply"])
    (tmp_path / "runs.jsonl").write_text('{"run": "r1"}\n')
    bad = CliRunner().invoke(cli.app, ["undo", "r1", "--apply"])

    assert missing.exit_code == 1
    assert str(missing.exception).startswith("no applied run has been logged yet")
    assert bad.exit_code == 1
    assert str(bad.exception).endswith(
        "line 1 is not a run-log line; fix or delete it, then undo again"
    )


def test_undo_dry_run_prints_the_plan_and_never_signs_in(tmp_path, monkeypatch):
    _, _, run_id = applied_run(tmp_path, monkeypatch)
    no_sign_in(tmp_path, monkeypatch)
    log = (tmp_path / "runs.jsonl").read_text()

    result = CliRunner().invoke(cli.app, ["--plain", "undo", run_id])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "planned id=m1 restore=INBOX,UNREAD remove=Receipts,Sorter/_processed",
        "planned id=m2 restore=INBOX,UNREAD remove=Receipts,Sorter/_processed",
        f"undo run={run_id} mode=dry-run undone=2 skipped=0",
    ]
    assert (tmp_path / "runs.jsonl").read_text() == log  # undo writes nothing to the log


def test_undo_on_a_terminal_prints_lines_not_records(capsys):
    plan = cli.UndoPlan("r1", (), (), ("m1",))

    cli._print_undo(plan, None, cli.CliOptions(plain=False, debug=False, json=False))

    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Undo · DRY RUN · run r1 · 0 to undo · 1 skipped"
    assert "undo run=" not in out


def test_undo_apply_restores_the_runs_labels(tmp_path, monkeypatch):
    mailbox, before, run_id = applied_run(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli.app, ["--plain", "undo", run_id, "--apply"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "undone id=m1 restore=INBOX,UNREAD remove=Receipts,Sorter/_processed",
        "undone id=m2 restore=INBOX,UNREAD remove=Receipts,Sorter/_processed",
        f"undo run={run_id} mode=apply undone=2 skipped=0",
    ]
    assert [email.label_ids for email in mailbox.emails] == before
    assert "Please visit this URL" in result.stderr  # sign-in output stays off stdout


def test_undo_apply_with_nothing_left_to_reverse_never_signs_in(tmp_path, monkeypatch):
    no_sign_in(tmp_path, monkeypatch)
    line = {"run": "r1", "id": "m1", "add": [], "add_ids": [], "remove": []}
    (tmp_path / "runs.jsonl").write_text(json.dumps(line) + "\n")

    result = CliRunner().invoke(cli.app, ["--plain", "undo", "r1", "--apply"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "skipped id=m1 reason=unchanged",
        "undo run=r1 mode=apply undone=0 skipped=1",
    ]


def test_a_gmail_error_mid_undo_prints_the_counts_then_says_how_to_finish(tmp_path, monkeypatch):
    mailbox, _, run_id = applied_run(tmp_path, monkeypatch)
    mailbox.fail_modify = {"m2"}

    result = CliRunner().invoke(cli.app, ["--plain", "undo", run_id, "--apply"])

    assert result.exit_code == 1
    assert result.stdout.splitlines()[-1] == f"undo run={run_id} mode=apply undone=1 skipped=0"
    assert str(result.exception) == (
        f"modify failed for 'm2'; undo stopped after 1 of 2 messages; "
        f"run inbox-sorter undo {run_id} --apply again to finish"
    )


def test_ctrl_c_mid_undo_says_how_to_finish(tmp_path, monkeypatch):
    mailbox, _, run_id = applied_run(tmp_path, monkeypatch)

    def interrupted(message_id, add_label_ids, remove_label_ids):
        raise KeyboardInterrupt

    mailbox.modify = interrupted

    result = CliRunner().invoke(cli.app, ["--plain", "undo", run_id, "--apply"])

    assert result.exit_code == 1
    assert result.stdout.splitlines() == [f"undo run={run_id} mode=apply undone=0 skipped=0"]
    assert str(result.exception) == (
        f"undo stopped after 0 of 2 messages; "
        f"run inbox-sorter undo {run_id} --apply again to finish"
    )


def test_ctrl_c_during_undo_sign_in_changes_nothing(tmp_path, monkeypatch):
    mailbox, _, run_id = applied_run(tmp_path, monkeypatch)

    def connect(state_dir):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    calls = len(mailbox.modify_calls)

    result = CliRunner().invoke(cli.app, ["--plain", "undo", run_id, "--apply"])

    assert result.exit_code == 1 and result.stdout == ""
    assert str(result.exception).startswith("undo stopped after 0 of 2 messages")
    assert len(mailbox.modify_calls) == calls
