"""execution.v1 Worker 核心路径测试，全部使用 mock runtime 不碰真实屏幕。"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any
from types import SimpleNamespace

import pytest

from douzero_advisor.execution_service.protocol import CONTRACT_VERSION
from douzero_advisor.execution_service.worker import ExecutionWorker


@dataclass
class MockHandRead:
    """模拟 HandReader 的输出。"""

    cards: tuple[str, ...]
    card_left_edges: tuple[int, ...] = ()
    card_tops: tuple[int, ...] = ()
    maximum_distance: float = 0.0
    recovered_slots: tuple[int, ...] = ()


@dataclass
class MockSelectionResult:
    """模拟 HandSelectionExecutor.select() 的返回。"""

    success: bool
    reason: str = ""


@dataclass
class MockButtonBox:
    center: tuple[int, int] = (0, 0)


@dataclass
class MockActionButtonRead:
    """模拟 ActionButtonReader 的输出。"""

    layout: str = "play_ready"
    buttons: dict[str, MockButtonBox] | None = None

    @property
    def actionable(self) -> bool:
        return self.layout != "unknown" and bool(self.buttons)


@dataclass
class MockLifecycleRead:
    """模拟局前生命周期按钮读取结果。"""

    stage: str
    texts: tuple[str, ...]
    buttons: tuple[Any, ...]


@dataclass
class MockOutcome:
    status: str  # clicked / cancelled / uncertain
    reason: str = ""


class MockSchedule:
    """模拟 ThreadedFinalClickScheduler 的 schedule 返回。"""

    def __init__(self, outcome: MockOutcome | None = None) -> None:
        self._outcome = outcome
        self.published = False
        self.cancelled = False

    def publish(self, point: tuple[int, int], observed_at: float) -> None:
        self.published = True

    def take_outcome(self) -> MockOutcome | None:
        return self._outcome

    def cancel(self) -> None:
        self.cancelled = True


class MockClickScheduler:
    """模拟点击调度器。"""

    def __init__(self, outcome: MockOutcome | None = None) -> None:
        self._outcome = outcome
        self.schedule_called = False

    def schedule(
        self,
        *,
        not_before: float,
        not_after: float,
        evidence_not_before: float,
        authority_check: Any,
    ) -> MockSchedule:
        self.schedule_called = True
        return MockSchedule(self._outcome)


class MockSelectionExecutor:
    """模拟选牌执行器。"""

    def __init__(self, result: MockSelectionResult) -> None:
        self._result = result
        self.select_calls = 0

    def select(self, hand: Any, recommended: Any) -> MockSelectionResult:
        self.select_calls += 1
        return self._result


class MockRuntime:
    """完整的 ExecutionRuntime mock，不碰任何真实屏幕。"""

    def __init__(
        self,
        *,
        hand: MockHandRead | None = None,
        selection_result: MockSelectionResult | None = None,
        buttons: MockActionButtonRead | None = None,
        verify_buttons: MockActionButtonRead | None = None,
        click_outcome: MockOutcome | None = None,
        capture_raises: bool = False,
        hover_capture_raises: bool = False,
        lifecycle_reads: list[MockLifecycleRead] | None = None,
    ) -> None:
        self._hand = hand
        self._selection_result = selection_result or MockSelectionResult(success=True)
        self._buttons = buttons or MockActionButtonRead(
            buttons={"play": MockButtonBox((720, 600))}
        )
        self._verify_buttons = verify_buttons
        self._click_outcome = click_outcome or MockOutcome(status="clicked")
        self._capture_raises = capture_raises
        self._hover_capture_raises = hover_capture_raises
        self._lifecycle_reads = lifecycle_reads or []
        self.lifecycle_read_calls = 0
        self.mouse_parks = 0
        self.mouse_after_click_parks = 0
        self.mouse_after_click_moves: list[tuple[int, int]] = []
        self.mouse = SimpleNamespace(
            click=lambda point: None,
            is_user_left_button_pressed=lambda: False,
            park=self._park_mouse,
            park_after_click=self._park_mouse_after_click,
            move_after_click=self._move_mouse_after_click,
        )
        self.capture_calls = 0
        self.button_read_calls = 0
        self.selection_executor = MockSelectionExecutor(self._selection_result)
        self.click_scheduler = MockClickScheduler(self._click_outcome)

    def capture_frame(self) -> Any:
        self.capture_calls += 1
        if self._capture_raises:
            from douzero_advisor.execution_service.runtime import ExecutionRuntimeError

            raise ExecutionRuntimeError("mock capture failure")
        if self._hover_capture_raises and self.capture_calls > 1:
            from douzero_advisor.execution_service.runtime import ExecutionRuntimeError

            raise ExecutionRuntimeError("mock hover capture failure")
        return f"frame-{self.capture_calls}"

    def read_hand(self, frame: Any) -> MockHandRead | None:
        return self._hand

    def read_action_buttons(self, frame: Any) -> MockActionButtonRead:
        self.button_read_calls += 1
        # 第一次读按钮（找按钮阶段）返回 buttons；第二次（验证阶段）返回 verify_buttons。
        if self.button_read_calls == 1:
            return self._buttons
        return self._verify_buttons if self._verify_buttons is not None else self._buttons

    def read_lifecycle_buttons(self, frame: Any) -> MockLifecycleRead:
        self.lifecycle_read_calls += 1
        if not self._lifecycle_reads:
            return MockLifecycleRead("unknown", (), ())
        return self._lifecycle_reads[min(self.lifecycle_read_calls - 1, len(self._lifecycle_reads) - 1)]

    def close(self) -> None:
        pass

    def _park_mouse(self) -> None:
        self.mouse_parks += 1

    def _park_mouse_after_click(self) -> None:
        self.mouse_after_click_parks += 1

    def _move_mouse_after_click(self, point: tuple[int, int]) -> None:
        self.mouse_after_click_moves.append(point)


def _build_worker(runtime: MockRuntime) -> tuple[ExecutionWorker, io.StringIO]:
    """构建 Worker 并返回 (worker, stdout_buffer)。"""
    stdout = io.StringIO()
    worker = ExecutionWorker(
        runtime,  # type: ignore[arg-type]
        stdin=io.StringIO(),
        stdout=stdout,
        log=io.StringIO(),
    )
    return worker, stdout


def _extract_result(stdout: io.StringIO) -> dict:
    """从 Worker 输出中提取最后一行 RESULT。"""
    lines = stdout.getvalue().strip().splitlines()
    for line in reversed(lines):
        import json

        msg = json.loads(line)
        if msg.get("messageType") == "RESULT":
            return msg
    raise AssertionError("No RESULT message found")


def _play_cmd(
    request_id: str = "req-001",
    *,
    action: str = "PLAY",
    auto_submit: bool = True,
    click_delay_ms: int = 0,
) -> Any:
    from douzero_advisor.execution_service.protocol import parse_command

    payload = {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "SUBMIT",
        "taskType": "CARD_PLAY",
        "requestId": request_id,
        "dealId": "deal-001",
        "generation": 3,
        "deadlineMs": 10000,
        "localSeat": "landlord",
        "authoritativeHand": ["3", "3", "5", "X", "D"],
        "actionType": action,
        "autoSubmit": auto_submit,
    }
    if action == "PLAY":
        payload["recommendedCards"] = ["5"]
    elif click_delay_ms:
        payload["clickDelayMs"] = click_delay_ms
    return parse_command(payload)


def _preplay_cmd(action: str = "call", stage: str = "call") -> Any:
    """构造一个合法的局前按钮执行命令。"""
    from douzero_advisor.execution_service.protocol import parse_command

    return parse_command({
        "contractVersion": CONTRACT_VERSION,
        "messageType": "SUBMIT",
        "taskType": "PREPLAY_BUTTON",
        "requestId": "preplay-001",
        "dealId": "deal-001",
        "generation": 3,
        "deadlineMs": 1000,
        "stage": stage,
        "action": action,
    })


class TestCardPlay:
    """单次 CARD_PLAY 任务的核心路径。"""

    def test_play_auto_submit_false_selects_then_hovers_play_button(self) -> None:
        """自动选牌完成后仅悬停出牌按钮，用户仍须自行点击，且不调度提交。"""
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        runtime = MockRuntime(hand=hand)
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd(auto_submit=False))
        result = _extract_result(stdout)
        assert result["status"] == "OK"
        assert result["autoSubmitEcho"] is False
        assert runtime.selection_executor.select_calls == 1
        assert not runtime.click_scheduler.schedule_called
        assert runtime.mouse_after_click_moves == [(720, 600)]
        assert runtime.mouse_after_click_parks == 0

    def test_play_hover_failure_does_not_block_ok_handoff(self) -> None:
        """悬停读取失败只记录日志，选牌成功仍返回 OK 交由 Java 发 TURN_END。"""
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        runtime = MockRuntime(hand=hand, hover_capture_raises=True)
        worker, stdout = _build_worker(runtime)

        worker._handle_card_play(_play_cmd(auto_submit=False))

        result = _extract_result(stdout)
        assert result["status"] == "OK"
        assert runtime.selection_executor.select_calls == 1
        assert runtime.mouse_after_click_moves == []
        assert "出牌按钮悬停读取失败" in worker._log.getvalue()

    def test_play_auto_submit_true_selects_and_clicks(self) -> None:
        """autoSubmit=true：选牌成功后调度点击，验证按钮消失。"""
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        buttons = MockActionButtonRead(buttons={"play": MockButtonBox((720, 600))})
        verify_buttons = MockActionButtonRead(layout="unknown", buttons=None)
        runtime = MockRuntime(
            hand=hand, buttons=buttons, verify_buttons=verify_buttons
        )
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd(auto_submit=True))
        result = _extract_result(stdout)
        assert result["status"] == "OK"
        assert result["autoSubmitEcho"] is True
        assert runtime.click_scheduler.schedule_called
        assert runtime.mouse_after_click_parks == 1

    def test_hand_mismatch_rejected(self) -> None:
        """当前手牌与权威手牌不一致 → HAND_MISMATCH，不选牌不点击。"""
        hand = MockHandRead(cards=("3", "5", "5", "X", "D"))  # 缺一个 3
        runtime = MockRuntime(hand=hand)
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd())
        result = _extract_result(stdout)
        assert result["status"] == "FAILED"
        assert result["errorCode"] == "HAND_MISMATCH"
        assert runtime.selection_executor.select_calls == 0
        assert not runtime.click_scheduler.schedule_called

    def test_hand_unreadable_rejected(self) -> None:
        """无法读取手牌 → HAND_MISMATCH。"""
        runtime = MockRuntime(hand=None)
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd())
        result = _extract_result(stdout)
        assert result["status"] == "FAILED"
        assert result["errorCode"] == "HAND_MISMATCH"

    def test_select_executor_failure_mapped(self) -> None:
        """选牌器返回失败 → 映射为 VERIFY_FAILED。"""
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        runtime = MockRuntime(
            hand=hand,
            selection_result=MockSelectionResult(
                success=False, reason="clicked_card_not_selected"
            ),
        )
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd())
        result = _extract_result(stdout)
        assert result["status"] == "FAILED"
        assert result["errorCode"] == "VERIFY_FAILED"

    def test_user_intervention_mapped_to_dedicated_failure(self) -> None:
        """选牌期间检测到用户按下鼠标 → 映射为 USER_INTERVENTION，Java 侧不重试。"""
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        runtime = MockRuntime(
            hand=hand,
            selection_result=MockSelectionResult(
                success=False, reason="user_intervention"
            ),
        )
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd())
        result = _extract_result(stdout)
        assert result["status"] == "FAILED"
        assert result["errorCode"] == "USER_INTERVENTION"

    def test_pass_auto_submit_true_clicks_pass_button(self) -> None:
        """Pass + autoSubmit=true：跳过选牌，直接点击不出按钮。"""
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        buttons = MockActionButtonRead(buttons={"pass": MockButtonBox((720, 600))})
        verify_buttons = MockActionButtonRead(layout="unknown", buttons=None)
        runtime = MockRuntime(
            hand=hand, buttons=buttons, verify_buttons=verify_buttons
        )
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd(action="PASS", auto_submit=True))
        result = _extract_result(stdout)
        assert result["status"] == "OK"
        assert runtime.selection_executor.select_calls == 0  # PASS 不选牌
        assert runtime.click_scheduler.schedule_called
        assert runtime.mouse_after_click_parks == 1

    # 场景：模型明确 PASS 且“要不起”稳定可见，固定等待 1.5 秒后才点击，点击后立即交回 Java。
    def test_delayed_pass_clicks_cannot_beat_without_post_click_capture(self, monkeypatch) -> None:
        class Clock:
            def __init__(self) -> None:
                self.value = 0.0

            def __call__(self) -> float:
                return self.value

            def sleep(self, seconds: float) -> None:
                self.value += seconds

        clock = Clock()
        runtime = MockRuntime(
            hand=MockHandRead(cards=("3", "3", "5", "X", "D")),
            buttons=MockActionButtonRead(
                layout="cannot_beat_only",
                buttons={"cannot_beat": MockButtonBox((720, 600))},
            ),
        )
        monkeypatch.setattr(
            "douzero_advisor.execution_service.worker.time.sleep",
            clock.sleep,
        )
        worker, stdout = _build_worker(runtime)
        worker._clock = clock

        worker._handle_card_play(
            _play_cmd(action="PASS", auto_submit=True, click_delay_ms=1500)
        )

        result = _extract_result(stdout)
        assert result["status"] == "OK"
        assert clock.value == 1.5
        assert runtime.button_read_calls == 1
        assert runtime.capture_calls == 2

    # 场景：自动不出等待期间用户按住左键接管。预期：零点击并返回 USER_INTERVENTION。
    def test_delayed_pass_stops_when_user_holds_the_mouse_button(self) -> None:
        runtime = MockRuntime(hand=MockHandRead(cards=("3", "3", "5", "X", "D")))
        runtime.mouse = SimpleNamespace(
            click=lambda point: None,
            is_user_left_button_pressed=lambda: True,
        )
        worker, stdout = _build_worker(runtime)

        worker._handle_card_play(
            _play_cmd(action="PASS", auto_submit=True, click_delay_ms=1500)
        )

        result = _extract_result(stdout)
        assert result["status"] == "FAILED"
        assert result["errorCode"] == "USER_INTERVENTION"
        assert not runtime.click_scheduler.schedule_called

    def test_pass_auto_submit_false_skips_everything(self) -> None:
        """Pass + autoSubmit=false：跳过选牌和点击，直接返回 OK。"""
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        runtime = MockRuntime(hand=hand)
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd(action="PASS", auto_submit=False))
        result = _extract_result(stdout)
        assert result["status"] == "OK"
        assert runtime.selection_executor.select_calls == 0
        assert not runtime.click_scheduler.schedule_called

    def test_button_not_found_rejected(self) -> None:
        """autoSubmit=true 但找不到动作按钮 → BUTTON_NOT_FOUND。"""
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        buttons = MockActionButtonRead(layout="unknown", buttons=None)
        runtime = MockRuntime(hand=hand, buttons=buttons)
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd(action="PASS", auto_submit=True))
        result = _extract_result(stdout)
        assert result["status"] == "FAILED"
        assert result["errorCode"] == "BUTTON_NOT_FOUND"
        assert not runtime.click_scheduler.schedule_called

    # 场景：PASS 点击已发出但 Worker 不读取点击后画面。预期：立即 OK，由 Java TURN_END 唯一确认结果。
    def test_pass_click_returns_ok_without_post_click_capture(self) -> None:
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        buttons = MockActionButtonRead(buttons={"pass": MockButtonBox((720, 600))})
        verify_buttons = MockActionButtonRead(buttons={"pass": MockButtonBox((720, 600))})
        runtime = MockRuntime(
            hand=hand,
            buttons=buttons,
            verify_buttons=verify_buttons,
            click_outcome=MockOutcome(status="clicked"),
        )
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd(action="PASS", auto_submit=True))
        result = _extract_result(stdout)
        assert result["status"] == "OK"
        assert runtime.button_read_calls == 1
        assert runtime.capture_calls == 2

    def test_click_outcome_cancelled_rejected(self) -> None:
        """点击被取消 → FAILED + EVIDENCE_STALE。"""
        hand = MockHandRead(cards=("3", "3", "5", "X", "D"))
        buttons = MockActionButtonRead(buttons={"pass": MockButtonBox((720, 600))})
        runtime = MockRuntime(
            hand=hand,
            buttons=buttons,
            click_outcome=MockOutcome(status="cancelled", reason="stale evidence"),
        )
        worker, stdout = _build_worker(runtime)
        worker._handle_card_play(_play_cmd(action="PASS", auto_submit=True))
        result = _extract_result(stdout)
        assert result["status"] == "FAILED"
        assert result["errorCode"] == "EVIDENCE_STALE"


class TestPreplayButton:
    """验证局前正向按钮点击和点击后阶段确认。"""

    def test_positive_preplay_action_clicks_after_stable_reads(self) -> None:
        """叫地主按钮连续稳定三帧后点击，并在进入下一阶段后确认成功。"""
        from douzero_advisor.vision.action_button_reader import ButtonBox

        box = ButtonBox(395, 498, 534, 559, "orange")
        button = SimpleNamespace(text="call", distance=0.1, margin=0.2, box=box)
        target = MockLifecycleRead("preplay", ("call", "no_call"), (button,))
        playing = MockLifecycleRead("playing", ("play", "hint"), ())
        runtime = MockRuntime(lifecycle_reads=[target, target, target, playing])
        clicks: list[tuple[int, int]] = []
        runtime.mouse.click = clicks.append
        worker, stdout = _build_worker(runtime)

        worker._handle_preplay_button(_preplay_cmd())

        result = _extract_result(stdout)
        assert result["status"] == "OK"
        assert clicks == [box.center]
        assert runtime.mouse_after_click_parks == 1

    def test_negative_preplay_action_is_not_accepted_by_protocol(self) -> None:
        """不加倍动作不能构造点击命令，防止 Worker 收到后误点。"""
        from douzero_advisor.execution_service.protocol import ContractError, parse_command

        with pytest.raises(ContractError, match="禁止自动点击"):
            parse_command({
                "contractVersion": CONTRACT_VERSION,
                "messageType": "SUBMIT",
                "taskType": "PREPLAY_BUTTON",
                "requestId": "preplay-negative",
                "dealId": "deal-001",
                "generation": 3,
                "deadlineMs": 1000,
                "stage": "double",
                "action": "no_double",
            })
