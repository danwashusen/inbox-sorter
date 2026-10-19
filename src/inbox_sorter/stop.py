"""Stopping a run safely: the stop flag, the Ctrl-C handler and the `q` key (ux.md §Stopping).

The first Ctrl-C or `q` asks the run to stop after the current message; a
Ctrl-C once a stop is pending raises KeyboardInterrupt, which aborts.
"""

import os
import select
import signal
import sys
import termios
import threading
import tty
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from types import FrameType

READER_NAME = "inbox-sorter-stop-keys"
STOPPING = "stopping after the current message; press Ctrl-C again to abort"


class StopFlag:
    """Set once when a stop is requested; the runner checks it before each message.

    A plain attribute, not a threading.Event: a signal handler can deadlock
    on Event's lock if the main thread holds it.
    """

    def __init__(self) -> None:
        self._requested = False

    @property
    def requested(self) -> bool:
        return self._requested

    def request(self) -> bool:
        """Request the stop; True only on the first call."""
        first = not self._requested
        self._requested = True
        return first


def write_stderr(text: str) -> None:
    """Write one line to stderr; safe in a signal handler, unlike print.

    A failed write is ignored: raised from the handler, it would end the run
    the user asked to stop gracefully.
    """
    try:
        os.write(2, (text + "\n").encode())
    except OSError:  # stderr closed, or a pipe whose reader has gone
        pass


def interrupt_handler(
    flag: StopFlag, notice: Callable[[str], None]
) -> Callable[[int, FrameType | None], None]:
    """A SIGINT handler: the first press requests the stop, a press once it is pending aborts."""

    def handle(signum: int, frame: FrameType | None) -> None:
        if flag.requested:  # by an earlier Ctrl-C or by q
            raise KeyboardInterrupt
        flag.request()
        notice(STOPPING)

    return handle


def read_keys(
    fd: int, flag: StopFlag, notice: Callable[[str], None], done: threading.Event
) -> None:
    """Request the stop on `q` or `Q`, until ``done`` is set, input ends or the terminal fails."""
    while not done.is_set():
        try:
            ready, _, _ = select.select([fd], [], [], 0.2)
            key = os.read(fd, 1) if ready else None
        # The terminal hung up, or a read would block because the terminal is
        # back in canonical mode as the run ends: q is off, Ctrl-C still works.
        except OSError:
            return
        if key is None:
            continue
        if not key:  # end of input
            return
        if key in (b"q", b"Q") and flag.request():
            notice(STOPPING)


@contextmanager
def listen_for_stop(flag: StopFlag, notice: Callable[[str], None] = write_stderr) -> Iterator[None]:
    """Handle Ctrl-C, and `q` too when stdin is this process's foreground terminal.

    A run in the background gets Ctrl-C handling only: changing or reading
    the terminal from a background process group would stop the process.
    Terminal access is best-effort: a terminal that hangs up loses the `q`
    listener, never the run's outcome. The reader reads its own non-blocking
    descriptor of the terminal, so it can never block the run's exit.
    """
    previous = signal.signal(signal.SIGINT, interrupt_handler(flag, notice))
    fd = sys.stdin.fileno() if _foreground_terminal() else None
    saved = None
    reader_fd: int | None = None
    done = threading.Event()
    reader: threading.Thread | None = None
    try:
        if fd is not None:
            try:
                # O_NONBLOCK on a private descriptor leaves stdin, shared with the shell, blocking.
                reader_fd = os.open(os.ttyname(fd), os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
                saved = termios.tcgetattr(fd)
                tty.setcbreak(fd)  # ISIG stays on, so Ctrl-C still sends SIGINT
            except (OSError, termios.error):  # terminal gone; Ctrl-C still works
                saved = None
                if reader_fd is not None:
                    os.close(reader_fd)
                    reader_fd = None
            else:
                reader = threading.Thread(
                    target=read_keys,
                    args=(reader_fd, flag, notice, done),
                    name=READER_NAME,
                    daemon=True,
                )
                reader.start()
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
        if saved is not None:
            with suppress(termios.error):  # a hung-up terminal has nothing to restore
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        done.set()
        if reader is not None:
            reader.join()
        if reader_fd is not None:
            os.close(reader_fd)


def _foreground_terminal() -> bool:
    """True when stdin is a terminal and this process is its foreground process group."""
    try:
        fd = sys.stdin.fileno()
        return os.isatty(fd) and os.tcgetpgrp(fd) == os.getpgrp()
    except (OSError, ValueError):  # not our controlling terminal, or stdin is closed or fake
        return False
