from __future__ import annotations

import threading
import time

from douzero_advisor.automation.click_scheduler import (
    ThreadedFinalClickScheduler,
)


def _take_outcome(schedule, *, timeout: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        outcome = schedule.take_outcome()
        if outcome is not None:
            return outcome
        time.sleep(0.001)
    raise AssertionError("scheduled click did not finish")


def test_threaded_scheduler_clicks_without_another_listener_tick() -> None:
    clicks = []
    clicked = threading.Event()

    def final_click(point, *, not_before, not_after):
        mouse_down_at = time.monotonic()
        assert not_before <= mouse_down_at <= not_after
        clicks.append((mouse_down_at, point))
        clicked.set()
        return mouse_down_at

    scheduler = ThreadedFinalClickScheduler(final_click)
    started_at = time.monotonic()
    schedule = scheduler.schedule(
        not_before=started_at + 0.03,
        not_after=started_at + 0.2,
        evidence_not_before=started_at,
        authority_check=lambda: True,
    )
    schedule.publish((719, 499), observed_at=time.monotonic())

    assert clicked.wait(0.4)
    outcome = _take_outcome(schedule)
    assert outcome.status == "clicked"
    assert clicks[0][1] == (719, 499)


def test_threaded_scheduler_waits_for_post_delay_preflight_evidence() -> None:
    clicks = []

    def final_click(point, *, not_before, not_after):
        mouse_down_at = time.monotonic()
        clicks.append((mouse_down_at, point))
        return mouse_down_at

    scheduler = ThreadedFinalClickScheduler(final_click)
    started_at = time.monotonic()
    schedule = scheduler.schedule(
        not_before=started_at + 0.02,
        not_after=started_at + 0.2,
        evidence_not_before=started_at,
        authority_check=lambda: True,
    )
    time.sleep(0.04)
    assert clicks == []
    published_at = time.monotonic()
    schedule.publish((719, 499), observed_at=published_at)

    outcome = _take_outcome(schedule)
    assert outcome.status == "clicked"
    assert clicks[0][0] >= published_at


def test_threaded_scheduler_cancel_prevents_mouse_down() -> None:
    clicks = []
    scheduler = ThreadedFinalClickScheduler(
        lambda point, **kwargs: clicks.append(point) or time.monotonic()
    )
    started_at = time.monotonic()
    schedule = scheduler.schedule(
        not_before=started_at + 0.03,
        not_after=started_at + 0.1,
        evidence_not_before=started_at,
        authority_check=lambda: True,
    )
    schedule.publish((719, 499), observed_at=time.monotonic())
    schedule.cancel()

    time.sleep(0.05)
    assert clicks == []
    assert schedule.take_outcome() is None


def test_threaded_scheduler_invalidates_old_evidence_before_mouse_down() -> None:
    clicks = []
    scheduler = ThreadedFinalClickScheduler(
        lambda point, **kwargs: clicks.append(point) or time.monotonic()
    )
    started_at = time.monotonic()
    schedule = scheduler.schedule(
        not_before=started_at + 0.05,
        not_after=started_at + 0.2,
        evidence_not_before=started_at,
        authority_check=lambda: True,
    )
    schedule.publish((719, 499), observed_at=time.monotonic())
    schedule.invalidate()

    time.sleep(0.07)
    assert clicks == []
    schedule.publish((720, 500), observed_at=time.monotonic())
    outcome = _take_outcome(schedule)

    assert outcome.status == "clicked"
    assert clicks == [(720, 500)]


def test_threaded_scheduler_rejects_stale_preflight_evidence() -> None:
    clicks = []
    scheduler = ThreadedFinalClickScheduler(
        lambda point, **kwargs: clicks.append(point) or time.monotonic(),
        maximum_evidence_age_seconds=0.01,
    )
    started_at = time.monotonic()
    schedule = scheduler.schedule(
        not_before=started_at + 0.03,
        not_after=started_at + 0.06,
        evidence_not_before=started_at,
        authority_check=lambda: True,
    )
    schedule.publish((719, 499), observed_at=time.monotonic())

    outcome = _take_outcome(schedule)
    assert outcome.status == "cancelled"
    assert outcome.reason == "fresh_preflight_unavailable"
    assert clicks == []
