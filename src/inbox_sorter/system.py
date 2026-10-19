"""macOS helpers for long runs: keep the Mac awake and post a notification (ux.md §Long runs).

Both run a system command as a subprocess and are best-effort: a missing
command never fails a run (C-few-dependencies).
"""

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

# The text travels as arguments, never spliced into the script, so a quote
# in an error message can't break or inject into it.
NOTIFY_SCRIPT = (
    "on run argv\n"
    "display notification (item 1 of argv) with title (item 2 of argv)\n"
    "end run"
)


@contextmanager
def keep_awake() -> Iterator[None]:
    """Block idle sleep until the block exits, or this process dies (`caffeinate -w`)."""
    try:
        process = subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:  # no caffeinate: run on, unprotected
        process = None
    try:
        yield
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # never mask the run's own exit
                pass


def notify(message: str, title: str = "inbox-sorter") -> None:
    """Post a macOS notification; a failure is ignored."""
    try:
        subprocess.run(
            ["osascript", "-e", NOTIFY_SCRIPT, message, title],
            timeout=10,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
