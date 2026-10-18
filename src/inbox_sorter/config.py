"""Load and validate inbox-sorter.toml into typed settings."""

import os
import tempfile
import tomllib
from dataclasses import astuple, dataclass, fields
from pathlib import Path
from typing import Any, Literal, get_args

from inbox_sorter.errors import UserError, error_reason
from inbox_sorter.types import NEEDS_REPLY_LABEL, SORTER_PREFIX, SYSTEM_LABELS, TODAY_LABEL

Action = Literal["label", "archive", "mark_read"]
Source = Literal["imported", "created"]

ACTIONS: tuple[str, ...] = get_args(Action)
SOURCES: tuple[str, ...] = get_args(Source)


class ConfigError(UserError):
    """The config file is missing or invalid."""


@dataclass(frozen=True)
class RunSettings:
    max_messages: int = 50
    dry_run: bool = True
    body_chars: int = 2000


@dataclass(frozen=True)
class FlagSettings:
    needs_reply_min: float = 0.7
    today_min_score: float = 1.5


@dataclass(frozen=True)
class Category:
    id: str
    gmail_label: str
    source: Source
    enabled: bool
    description: str
    actions: tuple[Action, ...]
    min_confidence: float


@dataclass(frozen=True)
class Config:
    run: RunSettings
    flags: FlagSettings
    categories: tuple[Category, ...]


TABLES = ("run", "flags", "category")

# Read from the current directory unless --config overrides it (A-config-location).
CONFIG_FILENAME = "inbox-sorter.toml"

# Every [[category]] key is required. "actions" is checked on its own.
CATEGORY_KEYS: dict[str, type] = {
    "id": str,
    "gmail_label": str,
    "source": str,
    "enabled": bool,
    "description": str,
    "actions": list,
    "min_confidence": float,
}

TYPE_NAMES = {bool: "a boolean", int: "an integer", float: "a number", str: "a string"}

# Allowed (lowest, highest) for numeric settings, by "table.key"; None is unbounded.
RANGES: dict[str, tuple[float, float | None]] = {
    "run.max_messages": (1, None),
    "run.body_chars": (0, None),
    "flags.needs_reply_min": (0, 1),  # a probability
    "flags.today_min_score": (0, 2),  # expected urgency score
    "category.min_confidence": (0, 1),
}


def resolve_config_path(override: Path | None, cwd: Path | None = None) -> Path:
    """The --config path when given, else inbox-sorter.toml in the current directory."""
    if override is not None:
        return override.expanduser()
    return (cwd or Path.cwd()) / CONFIG_FILENAME


def load_config(path: Path) -> Config:
    """Read the config at ``path``. Raises ConfigError naming the cause."""
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError:
        raise ConfigError(
            f"{path}: config file not found; "
            "run `inbox-sorter setup` to create it or pass --config PATH"
        )
    except OSError as error:
        raise ConfigError(f"{path}: cannot read the config file: {error_reason(error)}")
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML: {error}")
    except UnicodeDecodeError:
        raise ConfigError(f"{path}: the config file is not UTF-8 text")

    unknown = [key for key in data if key not in TABLES]
    if unknown:
        raise ConfigError(f"{path}: unknown table [{unknown[0]}]")

    raw_categories = data.get("category", [])
    if not isinstance(raw_categories, list) or not all(isinstance(c, dict) for c in raw_categories):
        raise ConfigError(f"{path}: category must be written as [[category]] tables")
    if not raw_categories:
        raise ConfigError(
            f"{path}: no [[category]] tables; add them as described in docs/prd.md §Configuration"
        )

    categories = tuple(
        _category(raw, position, path) for position, raw in enumerate(raw_categories, start=1)
    )
    ids: set[str] = set()
    label_owners: dict[str, str] = {}  # gmail_label -> category id
    for category in categories:
        if category.id in ids:
            raise ConfigError(f"{path}: duplicate category id {category.id!r}")
        ids.add(category.id)
        owner = label_owners.setdefault(category.gmail_label, category.id)
        if owner != category.id:
            raise ConfigError(
                f"{path}: categories {owner!r} and {category.id!r} share gmail_label "
                f"{category.gmail_label!r}"
            )

    return Config(
        run=_settings(RunSettings, data, "run", path),
        flags=_settings(FlagSettings, data, "flags", path),
        categories=categories,
    )


def render_config(config: Config) -> str:
    """The config as TOML that load_config reads back equal; no comments (A-config-writer)."""
    lines: list[str] = []
    for table, settings in (("run", config.run), ("flags", config.flags)):
        lines.append(f"[{table}]")
        lines += [
            f"{field.name} = {_toml(value)}"
            for field, value in zip(fields(settings), astuple(settings), strict=True)
        ]
        lines.append("")
    for category in config.categories:
        lines.append("[[category]]")
        lines += [f"{key} = {_toml(getattr(category, key))}" for key in CATEGORY_KEYS]
        lines.append("")
    return "\n".join(lines)


def write_config(path: Path, config: Config, previous: Config | None) -> None:
    """Replace the config at ``path`` with ``config``; the only way the config is written.

    Refuses when the file no longer loads to ``previous`` (its values changed
    since setup loaded it), or appeared when ``previous`` is None. The text is written to a temp file
    next to the file (a symlink's target, never the link) and read back with
    load_config before it replaces the file.
    """
    changed = ConfigError(f"{path} changed while setup ran; nothing was written; run setup again")
    if previous is None:
        if path.exists():
            raise changed
    else:
        try:
            current = load_config(path)
        except ConfigError:
            raise changed from None
        if current != previous:
            raise changed

    # Replace a symlink's target, not the link (the config may live in a dotfiles repo).
    target = path.resolve()
    temp: Path | None = None
    try:
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
        # A unique name, created exclusively: never a file or link already there.
        fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        temp = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(render_config(config))
        os.chmod(temp, mode)
        try:
            written = load_config(temp)
        except ConfigError as error:
            raise ConfigError(f"{path}: the rendered config does not load: {error}") from error
        if written != config:
            raise ConfigError(f"{path}: the rendered config does not read back equal")
        os.replace(temp, target)
    except OSError as error:
        raise ConfigError(f"{path}: cannot write the config file: {error_reason(error)}") from error
    except UnicodeError as error:  # a lone surrogate cannot be written as UTF-8
        raise ConfigError(f"{path}: cannot write the config file: {error.reason}") from error
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, tuple):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    return '"' + "".join(_toml_char(char) for char in value) + '"'


# TOML basic-string escapes; any other control character or DEL becomes \uXXXX.
TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_char(char: str) -> str:
    if char in TOML_ESCAPES:
        return TOML_ESCAPES[char]
    if ord(char) < 0x20 or ord(char) == 0x7F:
        return f"\\u{ord(char):04X}"
    return char


def _settings[T](cls: type[T], data: dict[str, Any], table: str, path: Path) -> T:
    """Build a settings table; omitted keys keep the dataclass defaults."""
    raw = data.get(table, {})
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: [{table}] must be a table")
    known = {field.name: field for field in fields(cls)}
    unknown = [key for key in raw if key not in known]
    if unknown:
        raise ConfigError(f"{path}: [{table}] has unknown key {unknown[0]!r}")
    return cls(
        **{
            key: _checked(f"{table}.{key}", value, known[key].type, f"{path}: [{table}] {key}")
            for key, value in raw.items()
        }
    )


def _category(raw: dict[str, Any], position: int, path: Path) -> Category:
    label = f"category {raw['id']!r}" if isinstance(raw.get("id"), str) else f"category #{position}"
    where = f"{path}: {label}"

    unknown = [key for key in raw if key not in CATEGORY_KEYS]
    if unknown:
        raise ConfigError(f"{where} has unknown key {unknown[0]!r}")
    missing = [key for key in CATEGORY_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"{where} is missing required key {missing[0]!r}")

    values = {
        key: _checked(f"category.{key}", raw[key], kind, f"{where}: {key}")
        for key, kind in CATEGORY_KEYS.items()
        if kind is not list
    }

    actions = raw["actions"]
    if not isinstance(actions, list) or not all(isinstance(action, str) for action in actions):
        raise ConfigError(f"{where}: actions must be a list of strings")
    for action in actions:
        if action not in ACTIONS:
            raise ConfigError(
                f"{where}: unknown action {action!r}; expected one of {', '.join(ACTIONS)}"
            )

    source = values["source"]
    if source not in SOURCES:
        raise ConfigError(
            f"{where}: unknown source {source!r}; expected one of {', '.join(SOURCES)}"
        )
    category = Category(actions=tuple(actions), **values)
    try:
        check_category(category)
    except ConfigError as error:
        raise ConfigError(f"{where}: {error}") from error
    return category


def check_category(category: Category) -> None:
    """Check a category's label rules and min_confidence; raises ConfigError without a path.

    ``load_config`` checks types first; ``setup`` calls this on each answer.
    """
    low, high = RANGES["category.min_confidence"]
    if not low <= category.min_confidence <= high:
        raise ConfigError(
            f"min_confidence must be between {low} and {high}, got {category.min_confidence!r}"
        )
    label = category.gmail_label
    if label in SYSTEM_LABELS:  # setup never imports these (prd.md §Setup step 1)
        raise ConfigError(f"gmail_label {label!r} is a Gmail system label, not a category")
    # Sorter/ holds only labels the tool owns (R-label-placement).
    under_sorter = label.startswith(SORTER_PREFIX)
    if category.source == "created":
        if not under_sorter:
            raise ConfigError(f"a created category's gmail_label must start with {SORTER_PREFIX!r}")
        name = label[len(SORTER_PREFIX) :]
        if not name:
            raise ConfigError(
                f"a created category's gmail_label needs a name after {SORTER_PREFIX!r}"
            )
        if name.startswith("_") or label in (NEEDS_REPLY_LABEL, TODAY_LABEL):
            raise ConfigError(f"gmail_label {label!r} is reserved for inbox-sorter's own labels")
    if category.source == "imported" and under_sorter:
        raise ConfigError(
            f"an imported category's gmail_label must not start with {SORTER_PREFIX!r}"
        )


def _checked(name: str, value: Any, kind: type, where: str) -> Any:
    """Check a value's type, then its range when RANGES bounds ``name``."""
    value = _value(value, kind, where)
    if name in RANGES:
        low, high = RANGES[name]
        if high is None and not value >= low:
            raise ConfigError(f"{where} must be at least {low}, got {value!r}")
        if high is not None and not low <= value <= high:
            raise ConfigError(f"{where} must be between {low} and {high}, got {value!r}")
    return value


def _value(value: Any, kind: type, where: str) -> Any:
    """Check a scalar's type.

    An int is accepted for a float; a bool never stands in for a number.
    """
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, bool) != (kind is bool) or not isinstance(value, kind):
        raise ConfigError(f"{where} must be {TYPE_NAMES[kind]}, got {value!r}")
    return value
