import io
import logging
import sys
from datetime import datetime, timedelta

from rich.console import Console

from inbox_sorter.stop import STOPPING
from inbox_sorter.summary import SummaryTally
from inbox_sorter.triage import decide
from inbox_sorter.types import (
    MessageDecided,
    MessageFailed,
    Retrying,
    RunFinished,
    RunStarted,
)
from inbox_sorter.ui.dashboard import (
    Dashboard,
    count_text,
    failed_text,
    feed_text,
    pace_text,
    retry_text,
    title_text,
)
from inbox_sorter.ui.table import TableReporter
from tests.factories import FakeClock, make_config, make_email, response

CONFIG = make_config()
AT = datetime(2026, 10, 17, 14, 2, 11)  # a Saturday


def test_count_text_with_and_without_a_total():
    assert count_text(6412, 10233) == "6,412 / 10,233   62%"
    assert count_text(2, 3) == "2 / 3   66%"  # floored
    assert count_text(12, None) == "12"
    assert count_text(0, 0) == "0 / 0"


def test_pace_text_with_a_rate():
    finish = AT + timedelta(hours=2, minutes=43)

    text = pace_text(23.4 / 60, 4 * 3600 + 34 * 60, 2 * 3600 + 43 * 60, finish)

    assert text == "23.4 msg/min · elapsed 4h 34m · ETA 2h 43m · Sat 16:45"


def test_pace_text_without_a_rate_or_an_eta():
    assert pace_text(None, 59, None, None) == "– msg/min · elapsed 59s · ETA –"
    assert pace_text(0.5, 61, None, None) == "30.0 msg/min · elapsed 1m 01s · ETA –"


def test_retry_text_counts_down_on_one_line():
    event = Retrying(None, attempt=2, attempts=5, delay=8.0, error="503\n\x1b[2Jgone")

    assert retry_text(event, 8.0) == "⟳ retrying in 8s (attempt 2 of 5): 503 ?[2Jgone"
    assert retry_text(event, 0.2).startswith("⟳ retrying in 1s ")
    assert retry_text(event, 0).startswith("⟳ retrying now (attempt 2 of 5)")
    assert retry_text(event, -3).startswith("⟳ retrying now ")


def test_feed_text_shows_time_sender_subject_category_confidence_and_set_flags():
    email = make_email(sender="Sarah Chen <sarah@x.example>", subject="Dinner", body="SECRET")
    decision = decide(email, response(confidence=0.5, reply=0.9), CONFIG, False)

    text = feed_text(AT, email, decision)

    assert text.startswith("14:02:11  Sarah Chen <sar…  Dinner")
    assert "receipts" in text and "50%" in text
    assert text.endswith("⚑ ?")
    assert "⏰" not in text and "SECRET" not in text


def test_feed_text_without_flags_has_no_icons():
    email = make_email()
    text = feed_text(AT, email, decide(email, response(), CONFIG, False))

    assert text.endswith("97%")


def test_failed_text_stays_on_one_line():
    text = failed_text(AT, MessageFailed("m2", "boom\nagain"))

    assert text == "14:02:11  ✗ m2 failed: boom again"


def test_title_text_names_the_mode_engine_and_device():
    dry = RunStarted("r1", dry_run=True, engine="clef-local", total=1)
    apply = RunStarted("r1", dry_run=False, engine="clef-local", total=1)

    assert title_text(None, "mps").plain == "inbox-sorter"
    assert title_text(dry, None).plain == "inbox-sorter · DRY RUN · clef-local"
    assert title_text(apply, "mps").plain == "inbox-sorter · APPLY · clef-local on MPS"


def test_dashboard_prints_the_table_rows_and_renders_the_panel():
    clock = FakeClock()
    tally = SummaryTally(CONFIG, clock=clock)
    out = io.StringIO()
    email = make_email(sender="Sarah Chen <sarah@x.example>", body="SECRET BODY TEXT")
    events = [
        Retrying(None, attempt=1, attempts=5, delay=2.0, error="503"),
        RunStarted("r1", dry_run=True, engine="clef-local", total=3),
        MessageDecided(email, decide(email, response(), CONFIG, False)),
        MessageDecided(make_email("m2"), decide(make_email("m2"), response(), CONFIG, False)),
        MessageFailed("m3", "no message 'm3'"),
        RunFinished("r1", decided=2, failed=1),
    ]
    expected = TableReporter(io.StringIO())
    rows = []

    with Dashboard(tally, out, device="mps", clock=clock, now=lambda: AT) as dashboard:
        before = io.StringIO()
        Console(file=before, width=100).print(dashboard.panel())  # before RunStarted
        for event in events:
            clock.now += 10
            tally(event)
            dashboard(event)
            rows += expected.lines(event)
        dashboard.notice(STOPPING)
        panel = io.StringIO()
        Console(file=panel, width=100).print(dashboard.panel())

    lines = out.getvalue().splitlines()
    assert [line for line in lines if line in rows] == rows
    assert "listing unprocessed inbox mail" in before.getvalue()
    rendered = panel.getvalue()
    assert "Sarah Chen" in rendered
    assert "m3 failed" in rendered
    assert STOPPING in rendered
    assert "SECRET" not in out.getvalue() + rendered


def test_feed_lines_are_cropped_not_wrapped_in_a_narrow_terminal():
    clock = FakeClock()
    tally = SummaryTally(CONFIG, clock=clock)
    dashboard = Dashboard(tally, io.StringIO(), clock=clock, now=lambda: AT)
    events = [RunStarted("r1", dry_run=True, engine="clef-local", total=9)]
    events += [
        MessageDecided(make_email(f"m{i}"), decide(make_email(f"m{i}"), response(), CONFIG, False))
        for i in range(3)
    ]
    for event in events:
        clock.now += 1
        tally(event)
        dashboard(event)
    narrow, wide = io.StringIO(), io.StringIO()

    Console(file=narrow, width=80).print(dashboard.panel())
    Console(file=wide, width=200).print(dashboard.panel())

    lines = narrow.getvalue().splitlines()
    assert len(lines) == len(wide.getvalue().splitlines())  # nothing wrapped
    assert sum("Order m" in line for line in lines) == 3


def test_the_retry_line_shows_while_a_retry_waits_and_clears_on_the_next_message():
    clock = FakeClock()
    tally = SummaryTally(CONFIG, clock=clock)
    dashboard = Dashboard(tally, io.StringIO(), clock=clock, now=lambda: AT)

    def rendered() -> str:
        out = io.StringIO()
        Console(file=out, width=100).print(dashboard.panel())
        return out.getvalue()

    for event in [
        RunStarted("r1", dry_run=True, engine="clef-local", total=2),
        Retrying("m1", attempt=1, attempts=5, delay=8.0, error="503 unavailable"),
    ]:
        tally(event)
        dashboard(event)
    clock.now += 3
    assert "⟳ retrying in 5s (attempt 1 of 5): 503 unavailable" in rendered()

    email = make_email()
    event = MessageDecided(email, decide(email, response(), CONFIG, False))
    tally(event)
    dashboard(event)
    assert "retrying" not in rendered()


def test_the_footer_offers_q_only_when_q_is_listened_for():
    dashboard = Dashboard(SummaryTally(CONFIG), io.StringIO())

    def footer() -> str:
        out = io.StringIO()
        Console(file=out, width=100).print(dashboard.panel())
        return out.getvalue().splitlines()[-1]

    assert "q stop after current" in footer()
    dashboard.q_key = False
    assert "Ctrl-C stop after current · twice to abort" in footer()
    dashboard.notice(STOPPING)
    assert STOPPING[:40] in footer()


class Terminal(io.StringIO):
    """Output that Rich takes for a terminal, so Live redirects sys.stdout and sys.stderr."""

    def isatty(self) -> bool:
        return True


def test_a_library_warning_prints_above_the_panel_not_past_it(monkeypatch):
    # A handler made before the run holds the real stderr, as transformers' does.
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    handler = logging.StreamHandler()
    logger = logging.getLogger("tests.library")
    logger.addHandler(handler)
    monkeypatch.setattr(logger, "propagate", False)
    out = Terminal()
    late = None
    try:
        with Dashboard(SummaryTally(CONFIG), out):
            logger.warning("falling back to a slower kernel")
            late = logging.StreamHandler()  # made during the run: holds the proxy
            logger.addHandler(late)
        logger.warning("after the run")
    finally:
        logger.removeHandler(handler)
        logger.removeHandler(late)

    assert "falling back to a slower kernel" in out.getvalue()
    assert stderr.getvalue() == "after the run\n" * 2  # both handlers have stderr back
