"""并行识别任务注册表。"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from douzero_advisor.recognition_service.protocol import (
    PromptEntryMode,
    RecognitionCommand,
    TaskType,
    TurnEntryMode,
    failure_message,
)
from douzero_advisor.recognition_service.tasks import RecognitionTask

ResultSink = Callable[[dict[str, Any]], None]
FinishedSink = Callable[[RecognitionCommand, dict[str, Any]], None]


class TaskRegistry:
    """管理请求唯一性、取消、截止时间和同帧结算抢占。

    ``last_prompt_by_deal`` 只保留上一个已交付的局前提示类型，用于后续
    request 的提示变化边沿；它不是牌局业务历史。
    """

    def __init__(
        self,
        result_sink: ResultSink,
        error_sink: Callable[[str], None] | None = None,
        *,
        finished_sink: FinishedSink | None = None,
        recent_request_limit: int = 4096,
        prompt_deal_limit: int = 64,
        slow_task_min_interval: float = 0.0,
    ) -> None:
        if recent_request_limit <= 0 or prompt_deal_limit <= 0:
            raise ValueError("registry retention limits must be positive")
        if slow_task_min_interval < 0:
            raise ValueError("slow_task_min_interval must be non-negative")
        self._result_sink = result_sink
        self._error_sink = error_sink or (lambda _message: None)
        self._finished_sink = finished_sink or (lambda _command, _result: None)
        self._tasks: dict[str, RecognitionTask] = {}
        self._recent_request_ids: OrderedDict[str, None] = OrderedDict()
        self._last_prompt_by_deal: OrderedDict[tuple[str, int], str] = OrderedDict()
        self._recent_request_limit = recent_request_limit
        self._prompt_deal_limit = prompt_deal_limit
        # 新局和结算等慢边界任务的最小采样间隔，单位为秒；0 表示不节流。
        self._slow_task_min_interval = float(slow_task_min_interval)

    @property
    def has_tasks(self) -> bool:
        return bool(self._tasks)

    @property
    def active_request_ids(self) -> frozenset[str]:
        return frozenset(self._tasks)

    @property
    def recent_request_count(self) -> int:
        return len(self._recent_request_ids)

    @property
    def prompt_deal_count(self) -> int:
        return len(self._last_prompt_by_deal)

    def submit(self, command: RecognitionCommand, now: float) -> None:
        if command.request_id in self._tasks or command.request_id in self._recent_request_ids:
            self._result_sink(
                failure_message(
                    command,
                    "DUPLICATE_REQUEST_ID",
                    "requestId was already submitted",
                )
            )
            return
        if (
            command.task_type is TaskType.PREPLAY_PROMPT
            and command.prompt_entry_mode is PromptEntryMode.REQUIRE_CHANGE_THEN_NEW
        ):
            assert command.deal_id is not None and command.generation is not None
            deal_key = (command.deal_id, command.generation)
            previous = self._last_prompt_by_deal.get(deal_key)
            if previous is not None:
                self._last_prompt_by_deal.move_to_end(deal_key)
            command = replace(command, previous_prompt_type=previous)
        assert command.deadline_ms is not None
        wait_for_turn_reentry = (
            command.task_type is TaskType.LOCAL_TURN
            and command.turn_entry_mode is TurnEntryMode.REQUIRE_EXIT_THEN_REENTER
        )
        task = RecognitionTask(
            command,
            float("inf") if wait_for_turn_reentry else now + command.deadline_ms / 1000.0,
            observe_deadline_started=not wait_for_turn_reentry,
        )
        # 慢边界任务（新局、结算）按配置的间隔采样，避免每帧执行 reader。
        if command.task_type in {TaskType.NEW_GAME, TaskType.SETTLEMENT}:
            task.min_observation_interval = self._slow_task_min_interval
        self._tasks[command.request_id] = task

    def cancel(self, command: RecognitionCommand) -> None:
        task = self._tasks.pop(command.request_id, None)
        if task is None:
            return
        self._remember_request(task.command.request_id)
        if task.command.task_type is TaskType.DEAL:
            self._forget_deal_sensors(task.command)
        result = failure_message(
            task.command, "TASK_CANCELLED", "recognition task was cancelled"
        )
        self._result_sink(result)
        self._finished_sink(task.command, result)

    def expire(self, now: float) -> None:
        expired = [task for task in self._tasks.values() if now >= task.deadline_at]
        for task in expired:
            self._tasks.pop(task.command.request_id, None)
            self._expire_task(task)

    def observe(self, facts: Any, monotonic: Callable[[], float]) -> None:
        # 同一帧先让结算旁路稳定器观察并发出结果；一旦完成，该 deal 的普通
        # 任务本帧不得再完成，保证 Java 首先收到抢占事实。
        settlement_deals: set[tuple[str, int]] = set()
        settlement_tasks = sorted(
            (
                task
                for task in self._tasks.values()
                if task.command.task_type is TaskType.SETTLEMENT
            ),
            key=lambda task: task.command.request_id,
        )
        for task in settlement_tasks:
            if self._expire_if_due(task, monotonic()):
                continue
            result = self._observe_task(task, facts, monotonic)
            if result is None:
                continue
            if self._expire_if_due(task, monotonic()):
                continue
            self._complete(task, result)
            assert task.command.deal_id is not None and task.command.generation is not None
            settlement_deals.add((task.command.deal_id, task.command.generation))

        normal_tasks = sorted(
            (
                task
                for task in self._tasks.values()
                if task.command.task_type is not TaskType.SETTLEMENT
            ),
            key=lambda task: task.command.request_id,
        )
        for task in normal_tasks:
            if self._expire_if_due(task, monotonic()):
                continue
            identity = (task.command.deal_id, task.command.generation)
            if identity in settlement_deals:
                continue
            result = self._observe_task(task, facts, monotonic)
            if result is not None and not self._expire_if_due(task, monotonic()):
                self._complete(task, result)

    def _observe_task(
        self, task: RecognitionTask, facts: Any, monotonic: Callable[[], float]
    ) -> dict[str, Any] | None:
        try:
            return task.observe(facts, monotonic())
        except Exception as error:  # noqa: BLE001 - reader plugins must fail closed
            task.reset_candidates()
            self._error_sink(
                f"reader_wait:{task.command.task_type.value}:"
                f"{type(error).__name__}:{error}"
            )
            return None

    def _complete(self, task: RecognitionTask, result: dict[str, Any]) -> None:
        self._tasks.pop(task.command.request_id, None)
        self._remember_request(task.command.request_id)
        if (
            task.command.task_type is TaskType.PREPLAY_PROMPT
            and result.get("status") == "OK"
        ):
            assert task.command.deal_id is not None and task.command.generation is not None
            deal_key = (task.command.deal_id, task.command.generation)
            self._last_prompt_by_deal[deal_key] = str(result["promptType"])
            self._last_prompt_by_deal.move_to_end(deal_key)
            while len(self._last_prompt_by_deal) > self._prompt_deal_limit:
                self._last_prompt_by_deal.popitem(last=False)
        elif task.command.task_type in {TaskType.DEAL, TaskType.SETTLEMENT}:
            self._forget_deal_sensors(task.command)
        self._result_sink(result)
        self._finished_sink(task.command, result)

    def _expire_if_due(self, task: RecognitionTask, now: float) -> bool:
        if now < task.deadline_at or task.command.request_id not in self._tasks:
            return False
        self._tasks.pop(task.command.request_id, None)
        self._expire_task(task)
        return True

    def _expire_task(self, task: RecognitionTask) -> None:
        self._remember_request(task.command.request_id)
        if task.command.task_type is TaskType.DEAL:
            self._forget_deal_sensors(task.command)
        result = failure_message(
            task.command,
            "DEADLINE_EXCEEDED",
            "recognition task exceeded deadlineMs",
        )
        self._result_sink(result)
        self._finished_sink(task.command, result)

    def _remember_request(self, request_id: str) -> None:
        self._recent_request_ids[request_id] = None
        self._recent_request_ids.move_to_end(request_id)
        while len(self._recent_request_ids) > self._recent_request_limit:
            self._recent_request_ids.popitem(last=False)

    def _forget_deal_sensors(self, command: RecognitionCommand) -> None:
        if command.deal_id is not None and command.generation is not None:
            deal_key = (command.deal_id, command.generation)
            self._last_prompt_by_deal.pop(deal_key, None)
