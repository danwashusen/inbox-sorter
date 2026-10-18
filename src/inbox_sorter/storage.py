"""Where inbox-sorter keeps its state: OAuth files and run logs."""

from pathlib import Path

from inbox_sorter.errors import UserError, error_reason


class StorageError(UserError):
    """The state directory cannot be created or made private."""


def state_dir(home: Path | None = None) -> Path:
    """Return the state directory, creating it readable only by the user."""
    path = (home or Path.home()) / "Library" / "Application Support" / "inbox-sorter"
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)  # also tighten a directory the user made, which holds the client secret
    except OSError as error:
        raise StorageError(f"cannot create the state directory {path}: {error_reason(error)}") from error
    return path
