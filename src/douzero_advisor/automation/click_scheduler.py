"""最终按钮点击的时间窗口调度；不确定的鼠标结果保持 fail-closed。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time
from typing import Literal, Protocol


class FinalClickClient(Protocol):
    def __call__(
        self,
        point: tuple[int, int],
        *,
        not_before: float,
        not_after: float,
    ) -> float: ...


ScheduledClickStatus = Literal["clicked", "cancelled", "uncertain"]


@dataclass(frozen=True, slots=True)
class ScheduledClickOutcome:
    status: ScheduledClickStatus
    reason: str
    mouse_down_at: float | None = None
    deadline_late_seconds: float | None = None


class FinalClickSchedule(Protocol):
    def publish(self, point: tuple[int, int], *, observed_at: float) -> None: ...

    def invalidate(self) -> None: ...

    def take_outcome(self) -> ScheduledClickOutcome | None: ...

    def cancel(self) -> None: ...


class FinalClickScheduler(Protocol):
    def schedule(
        self,
        *,
        not_before: float,
        not_after: float,
        evidence_not_before: float,
        authority_check: Callable[[], bool],
    ) -> FinalClickSchedule: ...


@dataclass(frozen=True, slots=True)
class _ClickEvidence:
    point: tuple[int, int]
    observed_at: float


class ThreadedFinalClickScheduler:
    """Run the physical click independently from capture/OCR polling cadence."""

    def __init__(
        self,
        final_click_client: FinalClickClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        maximum_evidence_age_seconds: float = 0.55,
    ) -> None:
        if maximum_evidence_age_seconds <= 0:
            raise ValueError("maximum_evidence_age_seconds must be positive")
        self._final_click_client = final_click_client
        self._clock = clock
        self._maximum_evidence_age_seconds = float(
            maximum_evidence_age_seconds
        )

    def schedule(
        self,
        *,
        not_before: float,
        not_after: float,
        evidence_not_before: float,
        authority_check: Callable[[], bool],
    ) -> FinalClickSchedule:
        if not_before > not_after:
            raise ValueError("click schedule window is invalid")
        return _ThreadedFinalClickSchedule(
            self._final_click_client,
            clock=self._clock,
            maximum_evidence_age_seconds=self._maximum_evidence_age_seconds,
            not_before=not_before,
            not_after=not_after,
            evidence_not_before=evidence_not_before,
            authority_check=authority_check,
        )


class _ThreadedFinalClickSchedule:
    def __init__(
        self,
        final_click_client: FinalClickClient,
        *,
        clock: Callable[[], float],
        maximum_evidence_age_seconds: float,
        not_before: float,
        not_after: float,
        evidence_not_before: float,
        authority_check: Callable[[], bool],
    ) -> None:
        self._final_click_client = final_click_client
        self._clock = clock
        self._maximum_evidence_age_seconds = maximum_evidence_age_seconds
        self._not_before = not_before
        self._not_after = not_after
        self._evidence_not_before = evidence_not_before
        self._authority_check = authority_check
        self._condition = threading.Condition()
        self._cancelled = False
        self._evidence: _ClickEvidence | None = None
        self._outcome: ScheduledClickOutcome | None = None
        self._outcome_taken = False
        self._thread = threading.Thread(
            target=self._run,
            name="douzero-final-click",
            daemon=True,
        )
        self._thread.start()

    def publish(self, point: tuple[int, int], *, observed_at: float) -> None:
        with self._condition:
            if self._cancelled or self._outcome is not None:
                return
            self._evidence = _ClickEvidence(point, observed_at)
            self._condition.notify_all()

    def invalidate(self) -> None:
        """Revoke previously published geometry after an inconclusive frame."""
        with self._condition:
            if self._cancelled or self._outcome is not None:
                return
            self._evidence = None
            self._condition.notify_all()

    def take_outcome(self) -> ScheduledClickOutcome | None:
        with self._condition:
            if self._outcome is None or self._outcome_taken:
                return None
            self._outcome_taken = True
            return self._outcome

    def cancel(self) -> None:
        with self._condition:
            if self._outcome is not None:
                return
            self._cancelled = True
            self._condition.notify_all()

    def _run(self) -> None:
        with self._condition:
            while True:
                if self._cancelled:
                    return
                now = self._clock()
                if now > self._not_after:
                    self._outcome = ScheduledClickOutcome(
                        "cancelled",
                        "fresh_preflight_unavailable",
                        deadline_late_seconds=now - self._not_after,
                    )
                    return
                evidence = self._evidence
                fresh = bool(
                    evidence is not None
                    and evidence.observed_at >= self._evidence_not_before
                    and now - evidence.observed_at
                    <= self._maximum_evidence_age_seconds
                )
                if now >= self._not_before and fresh:
                    self._execute(evidence)
                    return
                wake_at = (
                    self._not_before
                    if now < self._not_before
                    else self._not_after
                )
                self._condition.wait(timeout=max(0.001, wake_at - now))

    def _execute(self, evidence: _ClickEvidence) -> None:
        try:
            if not self._authority_check():
                self._outcome = ScheduledClickOutcome(
                    "cancelled", "scheduled_authority_drift"
                )
                return
        except Exception as error:
            self._outcome = ScheduledClickOutcome(
                "cancelled", _error_reason("scheduled_preflight", error)
            )
            return
        try:
            mouse_down_at = float(
                self._final_click_client(
                    evidence.point,
                    not_before=self._not_before,
                    not_after=self._not_after,
                )
            )
        except Exception as error:
            failed_at = self._clock()
            self._outcome = ScheduledClickOutcome(
                "uncertain",
                _error_reason("final_click", error),
                deadline_late_seconds=(
                    failed_at - self._not_after
                    if failed_at > self._not_after
                    else None
                ),
            )
            return
        if not self._not_before <= mouse_down_at <= self._not_after:
            self._outcome = ScheduledClickOutcome(
                "uncertain", "final_click_timestamp_out_of_window"
            )
            return
        self._outcome = ScheduledClickOutcome(
            "clicked", "final_button_invoked", mouse_down_at=mouse_down_at
        )


def _error_reason(stage: str, error: Exception) -> str:
    return f"{stage}:{type(error).__name__}:{error}"
