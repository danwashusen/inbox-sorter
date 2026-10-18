from pathlib import Path

import pytest

from inbox_sorter.config import (
    CONFIG_FILENAME,
    Category,
    Config,
    ConfigError,
    FlagSettings,
    RunSettings,
    load_config,
    render_config,
    resolve_config_path,
    write_config,
)
from tests.factories import ALERTS as ALERTS_CATEGORY
from tests.factories import RECEIPTS, make_config

# The example from PRD §Configuration.
PRD_EXAMPLE = """
[run]
max_messages = 50
dry_run = true # default; pass --apply to change Gmail
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
"""

ALERTS = """
[[category]]
id = "alerts"
gmail_label = "Sorter/Alerts"
source = "created"
enabled = true
description = "Automated alert or notice from a service"
actions = ["label"]
min_confidence = 0.0
"""


def load(tmp_path, text):
    path = tmp_path / "inbox-sorter.toml"
    path.write_text(text)
    return load_config(path)


def test_prd_example_loads(tmp_path):
    config = load(tmp_path, PRD_EXAMPLE)

    assert config == Config(
        run=RunSettings(max_messages=50, dry_run=True, body_chars=2000),
        flags=FlagSettings(needs_reply_min=0.7, today_min_score=1.5),
        categories=(
            Category(
                id="receipts",
                gmail_label="Receipts",
                source="imported",
                enabled=True,
                description="Order confirmation, shipping update, invoice or receipt",
                actions=("label", "archive", "mark_read"),
                min_confidence=0.85,
            ),
            Category(
                id="alerts",
                gmail_label="Sorter/Alerts",
                source="created",
                enabled=True,
                description="Automated alert or notice from a service",
                actions=("label",),
                min_confidence=0.0,
            ),
        ),
    )


def test_omitted_tables_and_keys_take_defaults(tmp_path):
    assert load(tmp_path, ALERTS).run == RunSettings()
    assert load(tmp_path, ALERTS).flags == FlagSettings()
    assert RunSettings().dry_run is True

    config = load(tmp_path, "[run]\nmax_messages = 10\n" + ALERTS)

    assert config.run == RunSettings(max_messages=10, dry_run=True, body_chars=2000)


def test_int_is_accepted_for_a_float(tmp_path):
    config = load(tmp_path, ALERTS.replace("min_confidence = 0.0", "min_confidence = 1"))

    assert config.categories[0].min_confidence == 1.0
    assert isinstance(config.categories[0].min_confidence, float)


def test_non_utf8_file_is_a_config_error(tmp_path):
    path = tmp_path / "inbox-sorter.toml"
    path.write_bytes(ALERTS.encode("utf-16"))

    with pytest.raises(ConfigError, match="not UTF-8"):
        load_config(path)


def test_unreadable_file_names_the_reason(tmp_path, monkeypatch):
    path = tmp_path / "inbox-sorter.toml"

    def broken_open(self, *args, **kwargs):
        raise OSError("device gone")  # strerror is None

    monkeypatch.setattr(type(path), "open", broken_open)

    with pytest.raises(ConfigError) as caught:
        load_config(path)

    assert str(caught.value) == f"{path}: cannot read the config file: device gone"


def test_the_processed_marker_is_reserved(tmp_path):
    from inbox_sorter.gmail import PROCESSED_LABEL

    with pytest.raises(ConfigError, match="is reserved"):
        load(tmp_path, ALERTS.replace('"Sorter/Alerts"', f'"{PROCESSED_LABEL}"'))


def test_missing_file_names_the_path_and_the_override(tmp_path):
    path = tmp_path / "missing.toml"

    with pytest.raises(ConfigError) as caught:
        load_config(path)

    assert str(path) in str(caught.value)
    assert "inbox-sorter setup" in str(caught.value)
    assert "--config PATH" in str(caught.value)


def test_default_path_is_the_config_in_the_current_directory(tmp_path):
    assert CONFIG_FILENAME == "inbox-sorter.toml"
    assert resolve_config_path(None, cwd=tmp_path) == tmp_path / "inbox-sorter.toml"


def test_override_is_used_with_home_expanded(tmp_path):
    assert resolve_config_path(tmp_path / "other.toml", cwd=tmp_path) == tmp_path / "other.toml"
    assert resolve_config_path(Path("~/sorter.toml")) == Path.home() / "sorter.toml"


def test_missing_default_config_names_its_absolute_path(tmp_path):
    with pytest.raises(ConfigError) as caught:
        load_config(resolve_config_path(None, cwd=tmp_path))

    assert str(caught.value).startswith(f"{tmp_path / 'inbox-sorter.toml'}: config file not found")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[run\n", "invalid TOML"),
        ("[run]\nmax_messages = 10\n", "no [[category]]"),
        (ALERTS + ALERTS, "duplicate category id 'alerts'"),
        (
            ALERTS.replace('["label"]', '["label", "delete"]'),
            "category 'alerts': unknown action 'delete'",
        ),
        (
            ALERTS.replace("min_confidence = 0.0", "min_confidence = 1.5"),
            "min_confidence must be between 0 and 1",
        ),
        (ALERTS.replace('"created"', '"borrowed"'), "unknown source 'borrowed'"),
        (
            ALERTS.replace('"Sorter/Alerts"', '"Alerts"'),
            "created category's gmail_label must start with 'Sorter/'",
        ),
        (
            ALERTS.replace('"created"', '"imported"'),
            "imported category's gmail_label must not start with 'Sorter/'",
        ),
        (
            ALERTS.replace('description = "Automated alert or notice from a service"\n', ""),
            "category 'alerts' is missing required key 'description'",
        ),
        (ALERTS.replace('id = "alerts"\n', ""), "category #1 is missing required key 'id'"),
        ('[run]\nmax_messages = "50"\n' + ALERTS, "[run] max_messages must be an integer"),
        ("[run]\nmax_messages = true\n" + ALERTS, "[run] max_messages must be an integer"),
        ("[run]\nmax_messages = 0\n" + ALERTS, "[run] max_messages must be at least 1"),
        ("[run]\nbody_chars = -1\n" + ALERTS, "[run] body_chars must be at least 0"),
        ("[flags]\nneeds_reply_min = 1.5\n" + ALERTS, "needs_reply_min must be between 0 and 1"),
        ("[flags]\ntoday_min_score = 3\n" + ALERTS, "today_min_score must be between 0 and 2"),
        (ALERTS.replace("enabled = true", "enabled = 1"), "enabled must be a boolean"),
        (
            ALERTS.replace("min_confidence = 0.0", "min_confidance = 0.0"),
            "category 'alerts' has unknown key 'min_confidance'",
        ),
        ("[run]\nmax_mesages = 10\n" + ALERTS, "[run] has unknown key 'max_mesages'"),
        ('[engine]\nkind = "clef-local"\n' + ALERTS, "unknown table [engine]"),
        (
            ALERTS + ALERTS.replace('id = "alerts"', 'id = "notices"'),
            "categories 'alerts' and 'notices' share gmail_label 'Sorter/Alerts'",
        ),
        (ALERTS.replace('"Sorter/Alerts"', '"Sorter/Today"'), "'Sorter/Today' is reserved"),
        (
            ALERTS.replace('"Sorter/Alerts"', '"Sorter/Needs reply"'),
            "'Sorter/Needs reply' is reserved",
        ),
        (
            ALERTS.replace('"created"', '"imported"').replace('"Sorter/Alerts"', '"INBOX"'),
            "'INBOX' is a Gmail system label",
        ),
        (
            ALERTS.replace('"created"', '"imported"').replace('"Sorter/Alerts"', '"TRASH"'),
            "'TRASH' is a Gmail system label",
        ),
        (
            ALERTS.replace('"created"', '"imported"').replace(
                '"Sorter/Alerts"', '"CATEGORY_PROMOTIONS"'
            ),
            "'CATEGORY_PROMOTIONS' is a Gmail system label",
        ),
        (
            ALERTS.replace('"Sorter/Alerts"', '"Sorter/_low-confidence"'),
            "'Sorter/_low-confidence' is reserved",
        ),
        (ALERTS.replace('"Sorter/Alerts"', '"Sorter/"'), "needs a name after 'Sorter/'"),
    ],
)
def test_invalid_config_names_the_cause(tmp_path, text, expected):
    with pytest.raises(ConfigError) as caught:
        load(tmp_path, text)

    message = str(caught.value)
    assert message.startswith(str(tmp_path / "inbox-sorter.toml"))
    assert expected in message


AWKWARD = 'He said "hi" \\ back\nline\ttab\x01ctrl\x7fdel\b\f\r café 日本 Work/Clients'


@pytest.mark.parametrize(
    "config",
    [
        make_config(),
        Config(
            run=RunSettings(max_messages=7, dry_run=False, body_chars=0),
            flags=FlagSettings(needs_reply_min=0.25, today_min_score=2.0),
            categories=(
                Category(
                    id="work-clients",
                    gmail_label="Work/Clients",
                    source="imported",
                    enabled=False,
                    description=AWKWARD,
                    actions=("label", "mark_read"),
                    min_confidence=1.0,
                ),
                ALERTS_CATEGORY,
                RECEIPTS,
            ),
        ),
    ],
)
def test_render_config_round_trips(tmp_path, config):
    assert load(tmp_path, render_config(config)) == config


def test_render_config_round_trips_the_prd_example(tmp_path):
    config = load(tmp_path, PRD_EXAMPLE)

    assert load(tmp_path, render_config(config)) == config


def test_render_config_writes_tables_and_keys_in_order():
    text = render_config(make_config(RECEIPTS))

    assert text.splitlines() == [
        "[run]",
        "max_messages = 50",
        "dry_run = true",
        "body_chars = 2000",
        "",
        "[flags]",
        "needs_reply_min = 0.7",
        "today_min_score = 1.5",
        "",
        "[[category]]",
        'id = "receipts"',
        'gmail_label = "Receipts"',
        'source = "imported"',
        "enabled = true",
        'description = "Order confirmation, shipping update, invoice or receipt"',
        'actions = ["label", "archive", "mark_read"]',
        "min_confidence = 0.85",
    ]


def test_write_config_creates_a_new_file_readable_by_all(tmp_path):
    path = tmp_path / "inbox-sorter.toml"

    write_config(path, make_config(), None)

    assert load_config(path) == make_config()
    assert path.stat().st_mode & 0o777 == 0o644
    assert [p.name for p in tmp_path.iterdir()] == ["inbox-sorter.toml"]


def test_write_config_replaces_a_file_keeping_its_mode(tmp_path):
    path = tmp_path / "inbox-sorter.toml"
    path.write_text(PRD_EXAMPLE)
    path.chmod(0o600)
    previous = load_config(path)

    write_config(path, make_config(RECEIPTS), previous)

    assert load_config(path) == make_config(RECEIPTS)
    assert path.stat().st_mode & 0o777 == 0o600


def test_write_config_refuses_a_file_edited_since_it_was_loaded(tmp_path):
    path = tmp_path / "inbox-sorter.toml"
    path.write_text(PRD_EXAMPLE)
    previous = load_config(path)
    edited = PRD_EXAMPLE.replace("max_messages = 50", "max_messages = 60")
    path.write_text(edited)

    with pytest.raises(ConfigError, match="changed while setup ran; nothing was written"):
        write_config(path, make_config(), previous)

    assert path.read_text() == edited
    assert [p.name for p in tmp_path.iterdir()] == ["inbox-sorter.toml"]


def test_write_config_refuses_a_file_that_appeared(tmp_path):
    path = tmp_path / "inbox-sorter.toml"
    path.write_text(PRD_EXAMPLE)

    with pytest.raises(ConfigError, match="changed while setup ran"):
        write_config(path, make_config(), None)

    assert path.read_text() == PRD_EXAMPLE


def test_a_failed_write_leaves_no_temp_file_and_names_the_path(tmp_path, monkeypatch):
    path = tmp_path / "inbox-sorter.toml"

    def replace(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("inbox_sorter.config.os.replace", replace)

    with pytest.raises(ConfigError) as caught:
        write_config(path, make_config(), None)

    assert str(caught.value) == f"{path}: cannot write the config file: Permission denied"
    assert list(tmp_path.iterdir()) == []


def test_write_config_refuses_a_config_that_does_not_load(tmp_path):
    path = tmp_path / "inbox-sorter.toml"
    bad = make_config(Category(**{**vars(RECEIPTS), "gmail_label": "INBOX"}))

    with pytest.raises(ConfigError, match="does not load"):
        write_config(path, bad, None)

    assert list(tmp_path.iterdir()) == []
