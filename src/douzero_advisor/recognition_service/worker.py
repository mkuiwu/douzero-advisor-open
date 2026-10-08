"""一个 CommandReader、一个 Queue 和一个 CaptureLoop 的常驻 Worker。"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from typing import Any, TextIO

from douzero_advisor.capture.errors import CaptureNotReadyError
from douzero_advisor.recognition_service.deal_recorder import DealRecorder
from douzero_advisor.recognition_service.scene_recorder import RecognitionSceneRecorder
from douzero_advisor.recognition_service.facts import FrameFacts
from douzero_advisor.recognition_service.protocol import (
    ContractError,
    MessageType,
    RecognitionCommand,
    TaskType,
    failure_message,
    parse_command,
    ready_message,
)
from douzero_advisor.recognition_service.registry import TaskRegistry


class CommandReader:
    """stdin 的唯一读取者；只做 JSON/合同解析并把命令放进队列。"""

    def __init__(
        self,
        stream: TextIO,
        commands: Queue[RecognitionCommand],
        errors: Queue[dict[str, Any] | str],
        stopped: Event,
    ) -> None:
        self._stream = stream
        self._commands = commands
        self._errors = errors
        self._stopped = stopped

    def run(self) -> None:
        for line in self._stream:
            if self._stopped.is_set():
                return
            raw: object = None
            try:
                raw = json.loads(line)
                command = parse_command(raw)
            except (json.JSONDecodeError, ContractError) as error:
                result = _malformed_result(raw, error)
                self._errors.put(result if result is not None else f"invalid_command:{error}")
                continue
            self._commands.put(command)
        self._stopped.set()


class RecognitionWorker:
    """持续捕获一次并把同一 FrameFacts 广播给所有活动任务。"""

    def __init__(
        self,
        *,
        capture: Any,
        observation_reader: Any,
        lifecycle_reader: Any,
        settlement_reader: Any,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        error_stream: TextIO = sys.stderr,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        idle_wait_seconds: float = 0.01,
        dump_dir: Path | str | None = None,
        deal_recording_dir: Path | str | None = None,
        slow_task_min_interval: float = 0.0,
        min_capture_interval: float = 0.0,
        park_cursor: Callable[[], None] | None = None,
    ) -> None:
        if idle_wait_seconds <= 0:
            raise ValueError("idle_wait_seconds must be positive")
        if min_capture_interval < 0:
            raise ValueError("min_capture_interval must be non-negative")
        self._capture = capture
        self._observation_reader = observation_reader
        self._lifecycle_reader = lifecycle_reader
        self._settlement_reader = settlement_reader
        self._input_stream = input_stream
        self._output_stream = output_stream
        self._error_stream = error_stream
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._idle_wait_seconds = idle_wait_seconds
        self._recorder = RecognitionSceneRecorder(
            Path(dump_dir) if dump_dir is not None else None
        )
        self._deal_recorder = DealRecorder(
            Path(deal_recording_dir) if deal_recording_dir is not None else None
        )
        self._commands: Queue[RecognitionCommand] = Queue()
        self._errors: Queue[dict[str, Any] | str] = Queue()
        self._stopped = Event()
        self.registry = TaskRegistry(
            self._write,
            lambda message: print(message, file=self._error_stream, flush=True),
            finished_sink=self._on_task_finished,
            slow_task_min_interval=slow_task_min_interval,
        )
        # 全局采集最小间隔（秒）：限制两轮 capture/observe 之间的最短时间，避免全速跑满一个 CPU 核。
        # 0 表示不节流；生产环境设为 0.1（100ms），测试默认 0 保持确定性。
        self._min_capture_interval = min_capture_interval
        self._last_active_step_at: float | None = None
        # 每个本方回合任务在首帧前归位鼠标；只移动不点击，避免遮挡动作结果区。
        self._park_cursor = park_cursor

    def run(self) -> int:
        self._write(ready_message())
        reader = CommandReader(
            self._input_stream,
            self._commands,
            self._errors,
            self._stopped,
        )
        thread = Thread(target=reader.run, name="recognition-command-reader", daemon=True)
        thread.start()
        while not self._stopped.is_set():
            self.step()
            if not self.registry.has_tasks:
                self._stopped.wait(self._idle_wait_seconds)
        self._deal_recorder.finish("worker_stopped")
        close = getattr(self._capture, "close", None)
        if callable(close):
            close()
        return 0

    def step(self) -> None:
        """处理当前命令并在有任务时最多捕获一帧，供确定性单测调用。"""

        self._drain_commands()
        now = self._monotonic()
        self.registry.expire(now)
        if not self.registry.has_tasks:
            return
        # 全局采集节流：确保两轮 capture/observe 之间至少间隔 min_capture_interval。
        # 命令处理和超时检查不受影响，只跳过截图和识别，避免 CPU 满载。
        # 必须阻塞等待剩余时间而非直接 return，否则主循环会立即再次调用 step() 形成忙循环。
        if (
            self._min_capture_interval > 0
            and self._last_active_step_at is not None
            and now - self._last_active_step_at < self._min_capture_interval
        ):
            remaining = self._min_capture_interval - (now - self._last_active_step_at)
            self._stopped.wait(max(remaining, 0.001))
            return
        try:
            frame = self._capture.capture()
        except Exception as error:  # noqa: BLE001 - capture backend must fail closed
            # 窗口未出现或几何闸门尚未打开是正常等待，不向 Java 重复刷 stderr。
            if not isinstance(error, CaptureNotReadyError):
                print(
                    f"capture_wait:{type(error).__name__}:{error}",
                    file=self._error_stream,
                    flush=True,
                )
            # 捕获失败时也要退避；否则没有窗口时会在一个 CPU 核上忙循环。
            self._stopped.wait(self._idle_wait_seconds)
            self.registry.expire(self._monotonic())
            return
        generation = getattr(self._capture, "generation", 0)
        facts = FrameFacts(
            frame,
            generation=generation if type(generation) is int else 0,
            observed_at=_utc_text(self._utc_now()),
            observation_reader=self._observation_reader,
            lifecycle_reader=self._lifecycle_reader,
            settlement_reader=self._settlement_reader,
        )
        self._recorder.record_frame(
            self.registry.active_request_ids,
            getattr(facts.frame, "pixels", facts.frame),
            facts.observed_at,
        )
        self._deal_recorder.record_frame(
            getattr(facts.frame, "pixels", facts.frame), facts.observed_at
        )
        # Reader 可能比 capture 更慢；Registry 会在每个 Reader 返回后再次读取时钟，
        # 截止时间已到时只能输出 DEADLINE_EXCEEDED，不能交付迟到的 OK。
        self.registry.observe(facts, self._monotonic)
        self.registry.expire(self._monotonic())
        # 整个 active step（capture + observe）完成后才更新时间戳，确保下一轮至少间隔 min_capture_interval。
        self._last_active_step_at = self._monotonic()

    def enqueue(self, command: RecognitionCommand) -> None:
        """测试/嵌入式调用把已校验命令放入同一个队列。"""

        self._commands.put(command)

    def stop(self) -> None:
        self._stopped.set()

    def _drain_commands(self) -> None:
        while True:
            try:
                result = self._errors.get_nowait()
            except Empty:
                break
            if isinstance(result, dict):
                self._write(result)
            else:
                print(result, file=self._error_stream, flush=True)
        while True:
            try:
                command = self._commands.get_nowait()
            except Empty:
                break
            if command.message_type is MessageType.SUBMIT:
                if command.task_type is TaskType.LOCAL_TURN and self._park_cursor is not None:
                    try:
                        self._park_cursor()
                    except Exception as error:  # noqa: BLE001 - 光标未归位时不能接受本方回合快照
                        self._write(failure_message(
                            command,
                            "OBSERVATION_UNCERTAIN",
                            f"识别前鼠标归位失败: {type(error).__name__}: {error}",
                        ))
                        continue
                if command.task_type is TaskType.DEAL:
                    assert command.deal_id is not None
                    try:
                        self._deal_recorder.start(command.deal_id, _utc_text(self._utc_now()))
                    except OSError as error:
                        # 录像是诊断附属能力；目录不可用不能终止正在进行的识别协议进程。
                        print(
                            f"deal_recording_unavailable:{type(error).__name__}:{error}",
                            file=self._error_stream,
                            flush=True,
                        )
                self.registry.submit(command, self._monotonic())
                if command.request_id in self.registry.active_request_ids:
                    self._recorder.begin(command.request_id)
            else:
                self.registry.cancel(command)

    def _on_task_finished(self, command: RecognitionCommand, result: dict[str, Any]) -> None:
        saved = self._recorder.finish(command, result)
        if saved is not None:
            print(f"recognition_scene:{saved}", file=self._error_stream, flush=True)
        if command.task_type is TaskType.SETTLEMENT and result.get("status") == "OK":
            finished = self._deal_recorder.finish(
                "settlement_detected", result.get("observedAt")
            )
            if finished is not None:
                print(f"deal_recording:{finished}", file=self._error_stream, flush=True)

    def _write(self, message: dict[str, Any]) -> None:
        self._output_stream.write(
            json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self._output_stream.flush()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _malformed_result(raw: object, error: Exception) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        try:
            task_type = TaskType(str(raw.get("taskType")))
            deal_id = None
            generation = None
            if task_type is not TaskType.NEW_GAME:
                deal_id = raw.get("dealId")
                generation = raw.get("generation")
                if (
                    not isinstance(deal_id, str)
                    or not deal_id
                    or type(generation) is not int
                    or generation <= 0
                ):
                    return None
            command = RecognitionCommand(
                message_type=MessageType.SUBMIT,
                task_type=task_type,
                request_id=str(raw.get("requestId") or "UNKNOWN"),
                deal_id=deal_id,
                generation=generation,
            )
            return failure_message(command, "INVALID_REQUEST", str(error))
        except (TypeError, ValueError):
            return None
    return None
