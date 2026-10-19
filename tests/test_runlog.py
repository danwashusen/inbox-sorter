import json
import stat
from dataclasses import replace

import pytest

from inbox_sorter.runlog import RunLog, RunLogError, log_line
from inbox_sorter.types import Decision, Probabilities

DECISION = Decision(
    message_id="m1",
    category="receipts",
    confidence=0.97,
    needs_reply=False,
    today=False,
    low_confidence=False,
    used_body=True,
    add_labels=("Receipts", "Sorter/_processed"),
    remove_labels=("INBOX", "UNREAD"),
    probabilities=Probabilities({"receipts": 0.97, "alerts": 0.03}, 0.1, (0.8, 0.2, 0.0)),
)
ADDED = {"Receipts": "Label_5", "Sorter/_processed": "Label_9"}


def test_log_line_holds_the_decision_in_a_fixed_key_order():
    line = json.loads(log_line("r1", "clef-local", DECISION, ADDED))

    assert list(line) == [
        "run", "engine", "id", "category", "confidence", "probabilities",
        "reply", "today", "low", "body", "add", "add_ids", "remove",
    ]  # fmt: skip
    assert line["run"] == "r1" and line["engine"] == "clef-local" and line["id"] == "m1"
    assert line["probabilities"] == {
        "category": {"receipts": 0.97, "alerts": 0.03},
        "needs_reply": 0.1,
        "urgency": [0.8, 0.2, 0.0],
    }
    assert line["add"] == ["Receipts", "Sorter/_processed"]
    assert line["add_ids"] == ["Label_5", "Label_9"]
    assert line["remove"] == ["INBOX", "UNREAD"]
    assert line["body"] is True  # whether the body was read, never its text
    assert not {"sender", "subject", "snippet"} & set(line)


def test_log_line_writes_null_without_probabilities():
    line = json.loads(log_line("r1", "fake", replace(DECISION, probabilities=None), {}))

    assert line["probabilities"] is None and line["add"] == [] and line["add_ids"] == []


def test_log_line_is_one_line_and_keeps_non_ascii_names():
    line = log_line("r1", "fake", DECISION, {"Reçus\nx": "Label_5"})

    assert "\n" not in line and "Reçus" in line


def test_append_writes_one_private_line_per_call_across_instances(tmp_path):
    path = tmp_path / "runs.jsonl"

    RunLog(path).append("r1", "fake", DECISION, ADDED)
    RunLog(path).append("r2", "fake", DECISION, {})

    lines = path.read_text().split("\n")
    assert lines[2] == ""  # each line ends with a newline
    assert [json.loads(line)["run"] for line in lines[:2]] == ["r1", "r2"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_append_makes_an_existing_log_private(tmp_path):
    path = tmp_path / "runs.jsonl"
    path.write_text("")
    path.chmod(0o644)

    RunLog(path).append("r1", "fake", DECISION, ADDED)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_an_unwritable_log_raises_a_run_log_error_naming_it(tmp_path):
    path = tmp_path / "missing" / "runs.jsonl"

    with pytest.raises(RunLogError, match=f"cannot write the run log {path}"):
        RunLog(path).append("r1", "fake", DECISION, ADDED)
