import os
import subprocess

import pytest

from inbox_sorter import system


class Recorder:
    def __init__(self, raises=None):
        self.calls = []
        self.raises = raises

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args, 0)


def test_notify_passes_the_text_as_arguments_not_in_the_script(monkeypatch):
    run = Recorder()
    monkeypatch.setattr(system.subprocess, "run", run)

    system.notify("it's done")

    [(args, kwargs)] = run.calls
    assert args == ["osascript", "-e", system.NOTIFY_SCRIPT, "it's done", "inbox-sorter"]
    assert "done" not in system.NOTIFY_SCRIPT and "inbox-sorter" not in system.NOTIFY_SCRIPT
    assert kwargs["timeout"] == 10 and kwargs["check"] is False


@pytest.mark.parametrize(
    "error", [FileNotFoundError("osascript"), subprocess.TimeoutExpired("osascript", 10)]
)
def test_a_failed_notification_is_ignored(monkeypatch, error):
    monkeypatch.setattr(system.subprocess, "run", Recorder(raises=error))

    system.notify("done")  # does not raise


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.args = args
        self.terminated = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True


def test_keep_awake_runs_caffeinate_for_this_process_and_ends_it_on_exit(monkeypatch):
    started = []
    monkeypatch.setattr(system.subprocess, "Popen", lambda args, **kw: started.append(
        FakeProcess(args)) or started[-1])  # fmt: skip

    with system.keep_awake():
        [process] = started
        assert process.args == ["caffeinate", "-i", "-w", str(os.getpid())]
        assert not process.terminated

    assert process.terminated and process.waited


def test_keep_awake_ends_caffeinate_when_the_run_raises(monkeypatch):
    started = []
    monkeypatch.setattr(system.subprocess, "Popen", lambda args, **kw: started.append(
        FakeProcess(args)) or started[-1])  # fmt: skip

    with pytest.raises(RuntimeError), system.keep_awake():
        raise RuntimeError("run failed")

    assert started[0].terminated


def test_keep_awake_without_caffeinate_still_runs_the_block(monkeypatch):
    def missing(args, **kwargs):
        raise FileNotFoundError("caffeinate")

    monkeypatch.setattr(system.subprocess, "Popen", missing)
    ran = False

    with system.keep_awake():
        ran = True

    assert ran


def test_keep_awake_that_cannot_reap_caffeinate_does_not_mask_the_run(monkeypatch):
    class Stuck(FakeProcess):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("caffeinate", timeout)

    monkeypatch.setattr(system.subprocess, "Popen", lambda args, **kw: Stuck(args))

    with pytest.raises(RuntimeError, match="run failed"), system.keep_awake():
        raise RuntimeError("run failed")
