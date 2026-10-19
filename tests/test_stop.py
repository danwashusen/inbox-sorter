import os
import pty
import signal
import termios
import threading
import time

import pytest

from inbox_sorter import stop
from inbox_sorter.stop import STOPPING, StopFlag, interrupt_handler, listen_for_stop, read_keys


def test_request_is_true_only_the_first_time():
    flag = StopFlag()

    assert not flag.requested
    assert flag.request() is True
    assert flag.request() is False
    assert flag.requested


def test_the_first_interrupt_requests_a_stop_and_the_second_aborts():
    flag, notices = StopFlag(), []
    handle = interrupt_handler(flag, notices.append)

    handle(signal.SIGINT, None)

    assert flag.requested and notices == [STOPPING]
    with pytest.raises(KeyboardInterrupt):
        handle(signal.SIGINT, None)
    assert notices == [STOPPING]


def test_an_interrupt_after_q_aborts():
    flag, notices = StopFlag(), []
    flag.request()  # as q leaves it

    with pytest.raises(KeyboardInterrupt):
        interrupt_handler(flag, notices.append)(signal.SIGINT, None)
    assert notices == []


@pytest.mark.parametrize("keys, requested", [(b"xq", True), (b"xQq", True), (b"x", False)])
def test_read_keys_stops_on_q_and_returns_at_end_of_input(keys, requested):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, keys)
    os.close(write_fd)
    flag, notices = StopFlag(), []

    read_keys(read_fd, flag, notices.append, threading.Event())

    os.close(read_fd)
    assert flag.requested is requested
    assert notices == ([STOPPING] if requested else [])


@pytest.fixture
def terminal(monkeypatch):
    """stdin as a fresh pty slave; returns (master_fd, slave_fd)."""
    master, slave = pty.openpty()
    stdin = os.fdopen(slave, "r", closefd=False)
    monkeypatch.setattr(stop.sys, "stdin", stdin)
    yield master, slave
    stdin.close()
    os.close(slave)
    os.close(master)


def reader_threads():
    return [t for t in threading.enumerate() if t.name == stop.READER_NAME]


def test_in_the_foreground_q_is_read_in_cbreak_mode_and_everything_is_restored(
    terminal, monkeypatch
):
    master, slave = terminal
    # A fresh pty is not this process's controlling terminal, so tcgetpgrp would fail.
    monkeypatch.setattr(stop.os, "tcgetpgrp", lambda fd: os.getpgrp())
    before_attrs = termios.tcgetattr(slave)
    before_handler = signal.getsignal(signal.SIGINT)
    flag, notices = StopFlag(), []

    with pytest.raises(RuntimeError), listen_for_stop(flag, notices.append):
        assert not termios.tcgetattr(slave)[3] & termios.ICANON
        assert len(reader_threads()) == 1
        assert signal.getsignal(signal.SIGINT) is not before_handler
        os.write(master, b"q")
        deadline = time.monotonic() + 2
        while not flag.requested and time.monotonic() < deadline:
            time.sleep(0.01)
        assert flag.requested and notices == [STOPPING]
        raise RuntimeError("the run failed")

    assert termios.tcgetattr(slave) == before_attrs
    assert signal.getsignal(signal.SIGINT) is before_handler
    assert reader_threads() == []


@pytest.mark.parametrize("pgrp", ["other group", "not our terminal"])
def test_in_the_background_only_ctrl_c_is_handled(terminal, monkeypatch, pgrp):
    master, slave = terminal

    def tcgetpgrp(fd):
        if pgrp == "not our terminal":
            raise OSError("Inappropriate ioctl for device")
        return os.getpgrp() + 1

    monkeypatch.setattr(stop.os, "tcgetpgrp", tcgetpgrp)
    before_attrs = termios.tcgetattr(slave)
    before_handler = signal.getsignal(signal.SIGINT)

    with listen_for_stop(StopFlag(), lambda text: None):
        assert termios.tcgetattr(slave) == before_attrs
        assert reader_threads() == []
        assert signal.getsignal(signal.SIGINT) is not before_handler

    assert signal.getsignal(signal.SIGINT) is before_handler
