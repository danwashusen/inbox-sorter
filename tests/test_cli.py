import json

from typer.testing import CliRunner

from inbox_sorter import cli
from inbox_sorter.engines.fake import FakeEngine
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
