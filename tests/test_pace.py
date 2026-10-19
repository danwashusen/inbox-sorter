import pytest

from inbox_sorter.pace import RATE_WINDOW, Pace
from inbox_sorter.triage import decide
from inbox_sorter.types import (
    MessageDecided,
    MessageFailed,
    Retrying,
    RunFinished,
    RunStarted,
)
from tests.factories import FakeClock, make_config, make_email, response

EMAIL = make_email()
DECIDED = MessageDecided(EMAIL, decide(EMAIL, response(), make_config(), False))


def started(total: int | None = 10) -> RunStarted:
    return RunStarted("r1", dry_run=True, engine="fake", total=total)


def test_no_rate_or_eta_before_the_first_message():
    clock = FakeClock()
    pace = Pace(clock)
    assert pace.rate() is None and pace.eta() is None

    pace(started())
    clock.now += 5

    assert pace.rate() is None
    assert pace.eta() is None


def test_rate_after_one_message_runs_from_run_started():
    clock = FakeClock()
    pace = Pace(clock)
    pace(started(total=10))
    clock.now += 4
    pace(DECIDED)

    assert pace.rate() == pytest.approx(0.25)
    assert pace.eta() == pytest.approx(9 * 4)


def test_rate_reflects_only_the_last_window_and_follows_slowdowns():
    clock = FakeClock()
    pace = Pace(clock)
    pace(started(total=1000))
    for _ in range(RATE_WINDOW):
        clock.now += 1
        pace(DECIDED)
    assert pace.rate() == pytest.approx(1.0)

    for _ in range(RATE_WINDOW):
        clock.now += 10
        pace(DECIDED)

    assert pace.rate() == pytest.approx(0.1)  # the fast messages left the window


def test_eta_counts_down_between_events_and_floors_at_zero():
    clock = FakeClock()
    pace = Pace(clock)
    pace(started(total=3))
    clock.now += 10
    pace(DECIDED)
    assert pace.eta() == pytest.approx(20)

    clock.now += 5
    assert pace.eta() == pytest.approx(15)

    clock.now += 100
    assert pace.eta() == 0.0


def test_a_resumed_run_counts_only_the_remaining_mail():
    clock = FakeClock()
    pace = Pace(clock)
    pace(started(total=10))
    for _ in range(4):
        clock.now += 1
        pace(DECIDED)

    pace(started(total=3))  # the rerun after a stop lists only what is left
    clock.now += 2
    pace(DECIDED)

    assert (pace.done, pace.total) == (1, 3)
    assert pace.rate() == pytest.approx(0.5)
    assert pace.eta() == pytest.approx(4)


def test_failed_counts_as_done_and_other_events_change_nothing():
    clock = FakeClock()
    pace = Pace(clock)
    pace(started(total=5))
    clock.now += 2
    pace(MessageFailed("m2", "boom"))
    clock.now += 2
    pace(Retrying("m3", attempt=1, attempts=5, delay=2.0, error="503"))
    pace(RunFinished("r1", decided=0, failed=1))

    assert pace.done == 1
    assert pace.rate() == pytest.approx(0.5)


def test_eta_without_a_total_or_with_nothing_left():
    clock = FakeClock()
    unknown = Pace(clock)
    unknown(started(total=None))
    clock.now += 1
    unknown(DECIDED)
    assert unknown.rate() == pytest.approx(1.0)
    assert unknown.eta() is None

    finished = Pace(clock)
    finished(started(total=1))
    assert finished.eta() is None  # no rate yet, but mail is left
    finished(DECIDED)
    assert finished.eta() == 0.0

    assert Pace(clock).eta() is None  # before RunStarted


def test_a_zero_time_span_has_no_rate():
    clock = FakeClock()
    pace = Pace(clock)
    pace(started(total=5))
    pace(DECIDED)  # at the same instant

    assert pace.rate() is None
    assert pace.eta() is None
