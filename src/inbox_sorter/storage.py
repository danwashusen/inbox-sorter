"""Where inbox-sorter keeps its state: OAuth files and run logs."""

from pathlib import Path


def state_dir(home: Path | None = None) -> Path:
    """Return the state directory, creating it readable only by the user."""
    path = (home or Path.home()) / "Library" / "Application Support" / "inbox-sorter"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path
