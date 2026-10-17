"""Decision engine adapters. Pick one by kind with make_engine."""

from typing import Any

from inbox_sorter.engine import DecisionEngine

KINDS = ("clef-local", "fake")


def make_engine(kind: str, **options: Any) -> DecisionEngine:
    """Build the engine for a config ``kind``.

    Adapters are imported here, not at module load, so choosing the fake
    engine never imports torch.
    """
    if kind == "clef-local":
        from inbox_sorter.engines.clef_local import ClefLocalEngine

        return ClefLocalEngine(**options)
    if kind == "fake":
        from inbox_sorter.engines.fake import FakeEngine

        return FakeEngine(**options)
    raise ValueError(f"unknown engine kind {kind!r}; expected one of {', '.join(KINDS)}")
