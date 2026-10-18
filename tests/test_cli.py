import json
import os

import pytest

from typer.testing import CliRunner

from inbox_sorter import cli
from inbox_sorter.config import load_config
from inbox_sorter.engines.fake import FakeEngine
from inbox_sorter.gmail import LabelInfo
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
    assert [r["event"] for r in records] == ["started", "decided", "decided", "finished"]
    assert "Please visit this URL" in result.stderr
    assert "loading the clef model" in result.stderr


def test_no_enabled_category_names_the_config_file(tmp_path):
    config = tmp_path / "inbox-sorter.toml"
    config.write_text(CONFIG.replace("enabled = true", "enabled = false"))

    result = CliRunner().invoke(cli.app, ["run", "--config", str(config)])

    assert result.exit_code == 1
    assert str(result.exception).startswith(f"{config}: no category is enabled")


def setup_cli(tmp_path, monkeypatch, mailbox):
    def connect(state_dir):
        print("Please visit this URL to authorize this application: https://example")
        return mailbox

    monkeypatch.setattr(cli.GmailMailbox, "connect", staticmethod(connect))
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
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
