"""Load and validate inbox-sorter.toml into typed settings."""

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal, get_args

from inbox_sorter.errors import UserError

Action = Literal["label", "archive", "mark_read"]
Source = Literal["imported", "created"]

ACTIONS: tuple[str, ...] = get_args(Action)
SOURCES: tuple[str, ...] = get_args(Source)
CREATED_PREFIX = "Sorter/"  # created labels live under this parent (R-label-placement)


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

# Allowed (lowest, highest) for numeric settings; None is unbounded.
RANGES: dict[str, tuple[float, float | None]] = {
    "max_messages": (1, None),
    "body_chars": (0, None),
    "needs_reply_min": (0, 1),  # a probability
    "today_min_score": (0, 2),  # expected urgency score
    "min_confidence": (0, 1),
}


def load_config(path: Path) -> Config:
    """Read the config at ``path``. Raises ConfigError naming the cause."""
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError:
        raise ConfigError(f"{path}: config file not found; run `inbox-sorter setup` to create it")
    except OSError as error:
        raise ConfigError(f"{path}: cannot read the config file: {error.strerror}")
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
        raise ConfigError(f"{path}: no [[category]] tables; run `inbox-sorter setup` to add them")

    categories = tuple(
        _category(raw, position, path) for position, raw in enumerate(raw_categories, start=1)
    )
    seen: set[str] = set()
    for category in categories:
        if category.id in seen:
            raise ConfigError(f"{path}: duplicate category id {category.id!r}")
        seen.add(category.id)

    return Config(
        run=_settings(RunSettings, data, "run", path),
        flags=_settings(FlagSettings, data, "flags", path),
        categories=categories,
    )


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
            key: _checked(key, value, known[key].type, f"{path}: [{table}] {key}")
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
        key: _checked(key, raw[key], kind, f"{where}: {key}")
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
        raise ConfigError(f"{where}: unknown source {source!r}; expected one of {', '.join(SOURCES)}")
    under_sorter = values["gmail_label"].startswith(CREATED_PREFIX)
    if source == "created" and not under_sorter:
        raise ConfigError(f"{where}: a created category's gmail_label must start with {CREATED_PREFIX!r}")
    if source == "imported" and under_sorter:
        raise ConfigError(
            f"{where}: an imported category's gmail_label must not start with {CREATED_PREFIX!r}"
        )

    return Category(actions=tuple(actions), **values)


def _checked(key: str, value: Any, kind: type, where: str) -> Any:
    """Check a value's type, then its range when RANGES bounds the key."""
    value = _value(value, kind, where)
    if key in RANGES:
        low, high = RANGES[key]
        if high is None and not value >= low:
            raise ConfigError(f"{where} must be at least {low}, got {value!r}")
        if high is not None and not low <= value <= high:
            raise ConfigError(f"{where} must be between {low} and {high}, got {value!r}")
    return value


def _value(value: Any, kind: type, where: str) -> Any:
    """Check a scalar's type. An int is accepted for a float; a bool never stands in for a number."""
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, bool) != (kind is bool) or not isinstance(value, kind):
        raise ConfigError(f"{where} must be {TYPE_NAMES[kind]}, got {value!r}")
    return value
