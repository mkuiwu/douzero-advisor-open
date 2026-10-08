from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from douzero_advisor.capture.errors import CaptureNotReadyError
from douzero_advisor.recognition_service.protocol import parse_command
from douzero_advisor.recognition_service.registry import TaskRegistry
from douzero_advisor.recognition_service.worker import RecognitionWorker

HAND17 = ("D", "X", "2", "2", "A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "3")
HAND20 = HAND17 + ("A", "K", "Q")


@dataclass
class FakeFrame:
    pixels: dict


class FakeCapture:
    def __init__(self, frames):
        self.frames = list(frames)
        self.calls = 0
        self.generation = 1

    def capture(self):
        self.calls += 1
        value = self.frames.pop(0)
        if isinstance(value, tuple):
            self.generation, value = value
        return FakeFrame(value)


class FakeLifecycleReader:
    def read(self, frame):
        return SimpleNamespace(stage=frame.get("stage", "unknown"), texts=frame.get("texts", ()))


class FakeSettlementReader:
    def read(self, frame):
        names = frame.get("settlement", ())
        return SimpleNamespace(
            stage="settlement" if names else "unknown",
            buttons=tuple(SimpleNamespace(name=name) for name in names),
        )


class FakeTable:
    def __init__(self, cards):
        self._cards = cards

    def cards(self, region):
        return tuple(self._cards.get(region, ()))


class FakeObservationReader:
    def read_my_hand_with_diagnostics(self, frame):
        hand = frame.get("hand")
        return SimpleNamespace(value=SimpleNamespace(cards=hand) if hand else None)

    def read_bottom_cards_with_diagnostics(self, frame):
        bottom = frame.get("bottom")
        return SimpleNamespace(value=SimpleNamespace(cards=bottom) if bottom else None)

    def is_my_turn_ready(self, frame):
        return frame.get("ready")

    def local_action_buttons_present(self, frame):
        if "buttons" in frame:
            return frame["buttons"]
        return frame.get("ready")

    def read_self_turn_snapshot(self, frame):
        hand = frame.get("hand")
        return SimpleNamespace(
            hand=SimpleNamespace(value=SimpleNamespace(cards=hand) if hand else None),
            action_results=SimpleNamespace(states=frame.get("states", {})),
            table=SimpleNamespace(value=FakeTable(frame.get("cards", {}))),
        )


def command(task_type, request_id, **fields):
    value = {
        "contractVersion": "recognition.v1",
        "messageType": "SUBMIT",
        "taskType": task_type,
        "requestId": request_id,
        "deadlineMs": 10000,
    }
    if task_type != "NEW_GAME":
        value.update(dealId="deal-1", generation=1)
    value.update(fields)
    return parse_command(value)


def make_worker(
    frames,
    dump_dir=None,
    monotonic=None,
    slow_task_min_interval=0.0,
    min_capture_interval=0.0,
    park_cursor=None,
):
    output = io.StringIO()
    capture = FakeCapture(frames)
    worker = RecognitionWorker(
        capture=capture,
        observation_reader=FakeObservationReader(),
        lifecycle_reader=FakeLifecycleReader(),
        settlement_reader=FakeSettlementReader(),
        output_stream=output,
        monotonic=monotonic or (lambda: 0.0),
        utc_now=lambda: datetime(2026, 8, 26, tzinfo=UTC),
        dump_dir=dump_dir,
        slow_task_min_interval=slow_task_min_interval,
        min_capture_interval=min_capture_interval,
        park_cursor=park_cursor,
    )
    return worker, capture, output


# 验证游戏窗口尚未出现时只保持等待，不向 Java 重复输出同一条捕获日志。
def test_capture_not_ready_is_silent_and_backed_off() -> None:
    status = SimpleNamespace(state="waiting_for_window")

    class NotReadyCapture:
        generation = 1

        def capture(self):
            raise CaptureNotReadyError(status, "waiting_for_game")

    output = io.StringIO()
    errors = io.StringIO()
    worker = RecognitionWorker(
        capture=NotReadyCapture(),
        observation_reader=FakeObservationReader(),
        lifecycle_reader=FakeLifecycleReader(),
        settlement_reader=FakeSettlementReader(),
        output_stream=output,
        error_stream=errors,
        idle_wait_seconds=0.001,
    )
    waits: list[float] = []

    class StopSignal:
        def wait(self, timeout: float) -> bool:
            waits.append(timeout)
            return False

    worker._stopped = StopSignal()
    worker.enqueue(command("NEW_GAME", "new-1"))

    worker.step()

    assert output.getvalue() == ""
    assert errors.getvalue() == ""
    assert waits == [0.001]


# 验证录像目录创建失败时只报告录像降级，DEAL 识别任务仍可继续，不能导致 Python CV 进程退出。
def test_deal_recording_directory_failure_does_not_stop_recognition() -> None:
    frame = {"stage": "preplay", "texts": ("call", "no_call"), "hand": HAND17}
    errors = io.StringIO()
    worker = RecognitionWorker(
        capture=FakeCapture([frame]),
        observation_reader=FakeObservationReader(),
        lifecycle_reader=FakeLifecycleReader(),
        settlement_reader=FakeSettlementReader(),
        output_stream=io.StringIO(),
        error_stream=errors,
        monotonic=lambda: 0.0,
        utc_now=lambda: datetime(2026, 8, 26, tzinfo=UTC),
    )

    class UnavailableRecorder:
        def start(self, *_args) -> None:
            raise FileNotFoundError(3, "系统找不到指定的路径", "C:\\broken\\deal-recordings")

        def record_frame(self, *_args) -> None:
            return None

        def finish(self, *_args) -> None:
            return None

    worker._deal_recorder = UnavailableRecorder()
    worker.enqueue(command("DEAL", "deal-recording-unavailable"))

    worker.step()

    assert "deal-recording-unavailable" in worker.registry.active_request_ids
    assert "deal_recording_unavailable:FileNotFoundError" in errors.getvalue()


def result_lines(output):
    import json

    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_one_capture_loop_feeds_new_game_and_preplay_tasks() -> None:
    frame = {"stage": "preplay", "texts": ("call", "no_call"), "hand": HAND17}
    worker, capture, output = make_worker([frame, frame, frame])
    worker.enqueue(command("NEW_GAME", "new-1"))
    worker.enqueue(
        command(
            "PREPLAY_PROMPT",
            "prompt-1",
            entryMode="accept_current_stable_prompt",
        )
    )
    worker.step()
    worker.step()
    worker.step()
    assert capture.calls == 3
    results = result_lines(output)
    assert [result["taskType"] for result in results] == ["NEW_GAME", "PREPLAY_PROMPT"]
    assert results[1]["availableActions"] == ["call", "no_call"]


# 验证没有超级加倍按钮时，普通加倍和不加倍两个按钮仍可组成完整的局前识别结果。
def test_double_prompt_accepts_layout_without_super_double_button() -> None:
    frame = {"stage": "preplay", "texts": ("double", "no_double"), "hand": HAND17}
    worker, _capture, output = make_worker([frame, frame, frame])
    worker.enqueue(
        command(
            "PREPLAY_PROMPT",
            "double-without-super",
            entryMode="accept_current_stable_prompt",
        )
    )

    for _ in range(3):
        worker.step()

    result = result_lines(output)[0]
    assert result["promptType"] == "double"
    assert result["availableActions"] == ["double", "no_double"]


# 验证新局慢边界任务按配置间隔采样：未到间隔的帧被节流跳过，既不触发 lifecycle
# reader 执行，也不推进稳定计数，避免每帧视觉识别造成不必要的 CPU 占用；到达间隔
# 后才正常观察并累计稳定帧数。
def test_new_game_task_throttles_observation_interval() -> None:
    frame = {"stage": "preplay", "texts": ("call", "no_call"), "hand": HAND17}
    calls = {"lifecycle": 0}

    class CountingLifecycleReader(FakeLifecycleReader):
        def read(self, frame):
            calls["lifecycle"] += 1
            return super().read(frame)

    clock_value = [0.0]
    output = io.StringIO()
    worker = RecognitionWorker(
        capture=FakeCapture([frame] * 5),
        observation_reader=FakeObservationReader(),
        lifecycle_reader=CountingLifecycleReader(),
        settlement_reader=FakeSettlementReader(),
        output_stream=output,
        monotonic=lambda: clock_value[0],
        utc_now=lambda: datetime(2026, 8, 26, tzinfo=UTC),
        slow_task_min_interval=1.0,
    )
    worker.enqueue(command("NEW_GAME", "new-throttled"))
    # 第一次采样在 0.0 秒，reader 被调用一次，候选计数为 1。
    worker.step()
    assert calls["lifecycle"] == 1
    assert result_lines(output) == []
    # 0.5 秒未到 1 秒间隔，被节流跳过，reader 不被调用，候选计数不推进。
    clock_value[0] = 0.5
    worker.step()
    assert calls["lifecycle"] == 1
    assert result_lines(output) == []
    # 1.0 秒到达间隔，第二次采样，候选计数达到 2 满足稳定要求，返回新局确认。
    clock_value[0] = 1.0
    worker.step()
    assert calls["lifecycle"] == 2
    results = result_lines(output)
    assert len(results) == 1
    assert results[0]["taskType"] == "NEW_GAME"
    assert results[0]["status"] == "OK"


# 验证结算慢边界任务同样按配置间隔采样，未到间隔的帧不触发 settlement reader。
def test_settlement_task_throttles_observation_interval() -> None:
    frame = {"settlement": ("reveal_start", "continue_game"), "hand": HAND17}
    calls = {"settlement": 0}

    class CountingSettlementReader(FakeSettlementReader):
        def read(self, frame):
            calls["settlement"] += 1
            return super().read(frame)

    clock_value = [0.0]
    output = io.StringIO()
    worker = RecognitionWorker(
        capture=FakeCapture([frame] * 5),
        observation_reader=FakeObservationReader(),
        lifecycle_reader=FakeLifecycleReader(),
        settlement_reader=CountingSettlementReader(),
        output_stream=output,
        monotonic=lambda: clock_value[0],
        utc_now=lambda: datetime(2026, 8, 26, tzinfo=UTC),
        slow_task_min_interval=1.0,
    )
    worker.enqueue(command("SETTLEMENT", "settlement-throttled"))
    worker.step()
    assert calls["settlement"] == 1
    clock_value[0] = 0.5
    worker.step()
    assert calls["settlement"] == 1
    clock_value[0] = 1.0
    worker.step()
    assert calls["settlement"] == 2
    results = result_lines(output)
    assert len(results) == 1
    assert results[0]["taskType"] == "SETTLEMENT"
    assert results[0]["status"] == "OK"


# 验证全局采集最小间隔对所有任务生效：未到间隔时跳过 capture 并阻塞等待剩余时间，
# 到间隔后才实际截图识别，避免全速跑满一个 CPU 核。
def test_global_capture_interval_throttles_all_tasks() -> None:
    # stage=unknown 永远不满足新局确认条件，任务会持续活跃，适合验证采集节流。
    idle_frame = {"stage": "unknown", "hand": HAND17}
    clock_value = [0.0]
    worker, capture, _output = make_worker(
        [idle_frame] * 10,
        monotonic=lambda: clock_value[0],
        min_capture_interval=0.1,
    )
    # 拦截 wait 调用，验证节流跳过时确实阻塞等待剩余时间而非忙循环空转。
    waits: list[float] = []

    class StopSignal:
        def wait(self, timeout: float) -> bool:
            waits.append(timeout)
            return False

    worker._stopped = StopSignal()
    worker.enqueue(command("NEW_GAME", "new-global-throttle"))

    # 第一次在 0.0 秒采样，capture 被调用，不触发节流。
    worker.step()
    assert capture.calls == 1
    assert waits == []
    # 0.05 秒未到 100ms 间隔，被节流跳过：capture 不被调用，但必须阻塞等待约 0.05 秒。
    clock_value[0] = 0.05
    worker.step()
    assert capture.calls == 1
    assert len(waits) == 1
    assert abs(waits[0] - 0.05) < 0.001
    # 0.11 秒到达间隔，第二次采样，不触发节流。
    clock_value[0] = 0.11
    worker.step()
    assert capture.calls == 2
    assert len(waits) == 1
    # 0.15 秒未到下一个间隔，再次被跳过，阻塞等待约 0.06 秒。
    clock_value[0] = 0.15
    worker.step()
    assert capture.calls == 2
    assert len(waits) == 2
    assert abs(waits[1] - 0.06) < 0.001
    # 0.22 秒到达间隔，第三次采样。
    clock_value[0] = 0.22
    worker.step()
    assert capture.calls == 3


def test_followup_prompt_waits_for_old_prompt_change() -> None:
    call = {"stage": "preplay", "texts": ("call", "no_call"), "hand": HAND17}
    rob = {"stage": "preplay", "texts": ("rob", "no_rob"), "hand": HAND17}
    worker, _capture, output = make_worker([call] * 3 + [call] * 2 + [rob] * 3)
    worker.enqueue(command("PREPLAY_PROMPT", "p1", entryMode="accept_current_stable_prompt"))
    for _ in range(3):
        worker.step()
    worker.enqueue(command("PREPLAY_PROMPT", "p2", entryMode="require_change_then_new"))
    for _ in range(5):
        worker.step()
    results = result_lines(output)
    assert [(item["requestId"], item["promptType"]) for item in results] == [
        ("p1", "call"),
        ("p2", "rob"),
    ]


def test_followup_prompt_accepts_new_prompt_after_old_prompt_disappears() -> None:
    call = {"stage": "preplay", "texts": ("call", "no_call"), "hand": HAND17}
    empty = {"stage": "preplay", "texts": (), "hand": HAND17}
    rob = {"stage": "preplay", "texts": ("rob", "no_rob"), "hand": HAND17}
    worker, _capture, output = make_worker([call] * 3 + [empty] + [rob] * 3)
    worker.enqueue(command("PREPLAY_PROMPT", "p1", entryMode="accept_current_stable_prompt"))
    for _ in range(3):
        worker.step()
    worker.enqueue(command("PREPLAY_PROMPT", "p2", entryMode="require_change_then_new"))
    for _ in range(4):
        worker.step()
    assert [(item["requestId"], item["promptType"]) for item in result_lines(output)] == [
        ("p1", "call"),
        ("p2", "rob"),
    ]


def test_landlord_deal_cannot_complete_on_doubling_screen() -> None:
    doubling = {
        "stage": "preplay",
        "texts": ("double", "super_double", "no_double"),
        "hand": HAND20,
        "bottom": ("A", "K", "Q"),
        "ready": False,
    }
    playing = {**doubling, "stage": "playing", "texts": ("play", "hint"), "ready": True}
    worker, _capture, output = make_worker([doubling] * 3 + [playing] * 3)
    worker.enqueue(command("DEAL", "deal-task"))
    for _ in range(3):
        worker.step()
    assert result_lines(output) == []
    for _ in range(3):
        worker.step()
    result = result_lines(output)[0]
    assert result["localSeat"] == "landlord"
    assert result["landlordOpeningPlay"] == []


@pytest.mark.parametrize(
    ("region", "seat"),
    [("left_play", "landlord_down"), ("right_play", "landlord_up")],
)
def test_farmer_deal_requires_one_landlord_opening(region, seat) -> None:
    frame = {
        "stage": "unknown",
        "hand": HAND17,
        "bottom": ("A", "K", "Q"),
        "states": {region: "play"},
        "cards": {region: ("7",)},
    }
    worker, _capture, output = make_worker([frame] * 3)
    worker.enqueue(command("DEAL", f"deal-{seat}"))
    for _ in range(3):
        worker.step()
    result = result_lines(output)[0]
    assert result["localSeat"] == seat
    assert result["landlordOpeningPlay"] == ["7"]


def test_followup_local_turn_requires_explicit_false_before_sensor_change() -> None:
    previous = {
        "ready": True,
        "hand": HAND17,
        "states": {"left_play": "pass", "right_play": "unreadable"},
    }
    changed = {
        **previous,
        "states": {"left_play": "play", "right_play": "unreadable"},
        "cards": {"left_play": ("7",)},
    }
    unknown = {**previous, "ready": None}
    worker, _capture, output = make_worker(
        [previous, previous, unknown, changed, changed]
    )
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-previous",
            localSeat="landlord_up",
            requiredActors=["landlord_down"],
            turnEntryMode="accept_current_stable_turn",
        )
    )
    worker.step()
    worker.step()
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-changed",
            localSeat="landlord_up",
            requiredActors=["landlord_down"],
            turnEntryMode="require_exit_then_reenter",
        )
    )
    worker.step()
    worker.step()
    worker.step()

    results = result_lines(output)
    assert [(item["requestId"], item["actionsBySeat"]) for item in results] == [
        ("turn-previous", {"landlord_down": []}),
    ]
    assert "turn-changed" in worker.registry.active_request_ids


# 验证建议后的回合结束探测只比较 Java 传入的基线；用户尚未操作时不会把原本方回合重复交付为新回合。
def test_turn_end_waits_while_buttons_and_baseline_snapshot_are_unchanged() -> None:
    baseline = {
        "buttons": True,
        "hand": HAND17,
        "states": {"left_play": "pass", "right_play": "play"},
        "cards": {"right_play": ("7",)},
    }
    worker, _capture, output = make_worker([baseline, baseline, baseline])
    worker.enqueue(
        command(
            "TURN_END",
            "turn-end-still-waiting",
            localSeat="landlord_up",
            baselineHand=list(HAND17),
            baselineActionsBySeat={"landlord_down": [], "landlord": ["7"]},
        )
    )
    worker.step()
    worker.step()
    worker.step()

    assert result_lines(output) == []
    assert "turn-end-still-waiting" in worker.registry.active_request_ids


# 验证本方操作按钮连续两帧消失且手牌可读时（无论出牌减少还是 Pass 不变），
# 都能完成旧回合结束任务，Java 才能据此开启下一次本方回合探测。
def test_turn_end_completes_after_stable_button_departure() -> None:
    baseline = {"buttons": True, "hand": HAND20, "states": {}}
    # Pass 场景：按钮消失但手牌不变；只要手牌可读就说明画面已稳定，构成回合结束证据。
    departed = {"buttons": False, "hand": HAND20, "states": {}}
    worker, _capture, output = make_worker([baseline, departed, departed])
    worker.enqueue(
        command(
            "TURN_END",
            "turn-end-buttons-gone",
            localSeat="landlord",
            baselineHand=list(HAND20),
            baselineActionsBySeat={},
        )
    )
    worker.step()
    assert result_lines(output) == []
    worker.step()
    assert result_lines(output) == []
    worker.step()

    results = result_lines(output)
    assert [(item["taskType"], item["requestId"], item["status"])
            for item in results] == [("TURN_END", "turn-end-buttons-gone", "OK")]


# 验证飞机/炸弹出牌动画遮挡期间，即使出牌按钮暂时消失、手牌也读不到，
# TURN_END 也不会被假触发——必须等动画结束、手牌稳定可读后才确认回合结束。
def test_turn_end_ignores_animation_obscured_buttons_without_hand_change() -> None:
    baseline = {"buttons": True, "hand": HAND17, "states": {}}
    # 动画遮挡帧：按钮消失、手牌完全读不到（空列表表示 OCR 失败）
    obscured = {"buttons": False, "hand": (), "states": {}}
    worker, _capture, output = make_worker([baseline, obscured, obscured, obscured])
    worker.enqueue(
        command(
            "TURN_END",
            "turn-end-animation-obscured",
            localSeat="landlord",
            baselineHand=list(HAND17),
            baselineActionsBySeat={},
        )
    )
    for _ in range(4):
        worker.step()

    # 三帧动画遮挡都不应触发 TURN_END，任务仍在等待
    assert result_lines(output) == []
    assert "turn-end-animation-obscured" in worker.registry.active_request_ids


# 验证按钮样式未及时消失时，本方实际手牌减少或已知对手结果变化也能作为连续稳定的旧回合结束证据。
def test_turn_end_accepts_stable_hand_or_known_side_action_change() -> None:
    baseline = {
        "buttons": True,
        "hand": HAND17,
        "states": {"left_play": "pass"},
    }
    hand_changed = {**baseline, "hand": HAND17[:-1]}
    worker, _capture, output = make_worker([baseline, hand_changed, hand_changed])
    worker.enqueue(
        command(
            "TURN_END",
            "turn-end-hand-changed",
            localSeat="landlord_up",
            baselineHand=list(HAND17),
            baselineActionsBySeat={"landlord_down": []},
        )
    )
    for _ in range(3):
        worker.step()

    results = result_lines(output)
    assert [(item["taskType"], item["requestId"], item["status"])
            for item in results] == [("TURN_END", "turn-end-hand-changed", "OK")]


def test_followup_local_turn_accepts_identical_snapshot_after_exit() -> None:
    ready = {
        "ready": True,
        "hand": HAND17,
        "states": {
            "left_play": "pass",
            "right_play": "unreadable",
        },
    }
    exited = {**ready, "ready": False}
    worker, _capture, output = make_worker(
        [ready, ready, ready, exited, exited, ready, ready]
    )
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-previous",
            localSeat="landlord_up",
            requiredActors=["landlord_down"],
            turnEntryMode="accept_current_stable_turn",
        )
    )
    worker.step()
    worker.step()
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-after-exit",
            localSeat="landlord_up",
            requiredActors=["landlord_down"],
            turnEntryMode="require_exit_then_reenter",
        )
    )
    for _ in range(5):
        worker.step()
    results = result_lines(output)
    assert [result["requestId"] for result in results] == [
        "turn-previous",
        "turn-after-exit",
    ]
    assert results[1]["actionsBySeat"] == {"landlord_down": []}


def test_single_absent_button_frame_does_not_arm_local_turn_exit() -> None:
    ready = {
        "ready": True,
        "hand": HAND17,
        "states": {"left_play": "pass", "right_play": "unreadable"},
    }
    exited = {**ready, "ready": False}
    worker, _capture, output = make_worker(
        [ready, ready, ready, exited, ready, ready]
    )
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-previous",
            localSeat="landlord_up",
            requiredActors=["landlord_down"],
            turnEntryMode="accept_current_stable_turn",
        )
    )
    worker.step()
    worker.step()
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-flicker",
            localSeat="landlord_up",
            requiredActors=["landlord_down"],
            turnEntryMode="require_exit_then_reenter",
        )
    )
    for _ in range(4):
        worker.step()

    results = result_lines(output)
    assert [result["requestId"] for result in results] == ["turn-previous"]
    assert "turn-flicker" in worker.registry.active_request_ids


def test_local_turn_parks_cursor_before_its_first_capture() -> None:
    """本方回合任务必须先把鼠标移出动作区，再开始读取稳定快照。"""

    parked: list[str] = []
    ready = {
        "ready": True,
        "hand": HAND17,
        "states": {"left_play": "pass", "right_play": "unreadable"},
    }
    worker, _capture, _output = make_worker(
        [ready, ready], park_cursor=lambda: parked.append("parked")
    )
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-park-before-capture",
            localSeat="landlord_up",
            requiredActors=["landlord_down"],
            turnEntryMode="accept_current_stable_turn",
        )
    )

    worker.step()

    assert parked == ["parked"]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_followup_local_turn_deadline_starts_after_buttons_reenter() -> None:
    clock = _Clock()
    still_first_turn = {"buttons": True, "hand": HAND20, "states": {}}
    exited = {**still_first_turn, "buttons": False}
    worker, _capture, output = make_worker(
        [still_first_turn] * 4 + [exited, exited] + [still_first_turn] * 4,
        monotonic=clock,
    )
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-wait-next",
            localSeat="landlord",
            requiredActors=["landlord_down", "landlord_up"],
            turnEntryMode="require_exit_then_reenter",
            deadlineMs=5000,
        )
    )
    for _ in range(3):
        worker.step()
    clock.now = 12.0
    worker.step()
    assert result_lines(output) == []
    assert "turn-wait-next" in worker.registry.active_request_ids

    worker.step()
    worker.step()
    clock.now = 12.0
    worker.step()
    clock.now = 16.9
    worker.step()
    assert result_lines(output) == []
    assert "turn-wait-next" in worker.registry.active_request_ids

    clock.now = 17.1
    worker.step()
    results = result_lines(output)
    assert results[0]["requestId"] == "turn-wait-next"
    assert results[0]["status"] == "FAILED"
    assert results[0]["errorCode"] == "DEADLINE_EXCEEDED"


def test_empty_required_actors_ignores_unreadable_sides() -> None:
    frame = {
        "buttons": True,
        "hand": HAND20,
        "states": {"left_play": "unreadable", "right_play": "unreadable"},
        "cards": {"left_play": ("7",), "right_play": ("8",)},
    }
    worker, _capture, output = make_worker([frame, frame])
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-first-landlord",
            localSeat="landlord",
            requiredActors=[],
            turnEntryMode="accept_current_stable_turn",
        )
    )
    worker.step()
    worker.step()
    results = result_lines(output)
    assert results[0]["requestId"] == "turn-first-landlord"
    assert results[0]["status"] == "OK"
    assert results[0]["actionsBySeat"] == {}


def test_first_landlord_down_turn_ignores_unread_next_player() -> None:
    frame = {
        "buttons": True,
        "hand": HAND17,
        "states": {"left_play": "play", "right_play": "unreadable"},
        "cards": {"left_play": ("8", "8")},
    }
    worker, _capture, output = make_worker([frame, frame])
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-first-down",
            localSeat="landlord_down",
            requiredActors=["landlord"],
            turnEntryMode="accept_current_stable_turn",
        )
    )
    worker.step()
    worker.step()
    results = result_lines(output)
    assert results[0]["requestId"] == "turn-first-down"
    assert results[0]["status"] == "OK"
    assert results[0]["actionsBySeat"] == {"landlord": ["8", "8"]}


def test_local_turn_uses_action_buttons_not_countdown() -> None:
    countdown_only = {
        "ready": True,
        "buttons": False,
        "hand": HAND17,
        "states": {},
    }
    with_buttons = {
        "ready": False,
        "buttons": True,
        "hand": HAND17,
        "states": {},
    }
    worker, _capture, output = make_worker(
        [countdown_only, countdown_only, with_buttons, with_buttons]
    )
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-buttons",
            localSeat="landlord",
            requiredActors=[],
            turnEntryMode="accept_current_stable_turn",
        )
    )
    worker.step()
    worker.step()
    assert result_lines(output) == []
    worker.step()
    worker.step()

    results = result_lines(output)
    assert [result["requestId"] for result in results] == ["turn-buttons"]


def test_unreadable_required_actor_fails_whole_local_turn(tmp_path) -> None:
    frame = {
        "buttons": True,
        "hand": HAND17,
        "states": {"left_play": "pass", "right_play": "play"},
    }
    errors = io.StringIO()
    output = io.StringIO()
    capture = FakeCapture([frame, frame])
    worker = RecognitionWorker(
        capture=capture,
        observation_reader=FakeObservationReader(),
        lifecycle_reader=FakeLifecycleReader(),
        settlement_reader=FakeSettlementReader(),
        output_stream=output,
        error_stream=errors,
        monotonic=lambda: 0.0,
        utc_now=lambda: datetime(2026, 8, 26, tzinfo=UTC),
        dump_dir=tmp_path,
    )
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-unread",
            localSeat="landlord",
            requiredActors=["landlord_down"],
            turnEntryMode="accept_current_stable_turn",
        )
    )
    worker.step()
    worker.step()
    results = result_lines(output)
    assert results[0]["status"] == "FAILED"
    assert results[0]["errorCode"] == "OBSERVATION_UNCERTAIN"
    dumps = [path for path in tmp_path.iterdir() if path.is_dir() and path.name != ".staging"]
    assert len(dumps) == 1
    assert (dumps[0] / "manifest.json").is_file()
    assert "recognition_scene:" in errors.getvalue()


def test_capture_generation_resets_stability_candidate() -> None:
    frame = {"stage": "preplay", "texts": ("call", "no_call")}
    worker, capture, output = make_worker([frame, (2, frame), frame])
    worker.enqueue(command("NEW_GAME", "new-generation"))
    worker.step()
    worker.step()
    assert result_lines(output) == []
    worker.step()
    assert result_lines(output)[0]["status"] == "OK"
    assert capture.calls == 3


def test_settlement_second_frame_preempts_local_turn_completion() -> None:
    frame = {
        "settlement": ("reveal_start", "continue_game"),
        "ready": True,
        "hand": HAND17,
        "states": {},
    }
    worker, _capture, output = make_worker([frame, frame])
    worker.enqueue(command("SETTLEMENT", "settlement-1"))
    worker.enqueue(
        command(
            "LOCAL_TURN",
            "turn-1",
            localSeat="landlord",
            requiredActors=[],
            turnEntryMode="accept_current_stable_turn",
        )
    )
    worker.step()
    worker.step()
    results = result_lines(output)
    assert [item["taskType"] for item in results] == ["SETTLEMENT"]
    assert "turn-1" in worker.registry.active_request_ids


def test_cancel_is_idempotent_and_request_id_cannot_be_reused() -> None:
    worker, capture, output = make_worker([])
    submitted = command("NEW_GAME", "new-cancelled")
    cancelled = parse_command(
        {
            "contractVersion": "recognition.v1",
            "messageType": "CANCEL",
            "taskType": "NEW_GAME",
            "requestId": "new-cancelled",
        }
    )
    worker.enqueue(submitted)
    worker.enqueue(cancelled)
    worker.enqueue(cancelled)
    worker.step()
    worker.enqueue(submitted)
    worker.step()
    results = result_lines(output)
    assert [item["errorCode"] for item in results] == [
        "TASK_CANCELLED",
        "DUPLICATE_REQUEST_ID",
    ]
    assert capture.calls == 0


def test_registry_deadline_fails_without_partial_result() -> None:
    results = []
    registry = TaskRegistry(results.append)
    submitted = command("NEW_GAME", "new-timeout")
    registry.submit(submitted, 10.0)
    registry.expire(19.999)
    assert results == []
    registry.expire(20.0)
    assert results[0]["errorCode"] == "DEADLINE_EXCEEDED"
    assert not registry.has_tasks


def test_reader_completion_at_deadline_cannot_emit_late_ok() -> None:
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    class SlowLifecycleReader(FakeLifecycleReader):
        def __init__(self, clock):
            self.clock = clock
            self.calls = 0

        def read(self, frame):
            self.calls += 1
            if self.calls == 2:
                self.clock.value = 0.01
            return super().read(frame)

    clock = Clock()
    frame = {"stage": "preplay", "texts": ("call", "no_call")}
    output = io.StringIO()
    worker = RecognitionWorker(
        capture=FakeCapture([frame, frame]),
        observation_reader=FakeObservationReader(),
        lifecycle_reader=SlowLifecycleReader(clock),
        settlement_reader=FakeSettlementReader(),
        output_stream=output,
        monotonic=clock,
        utc_now=lambda: datetime(2026, 8, 26, tzinfo=UTC),
    )
    worker.enqueue(command("NEW_GAME", "late-new", deadlineMs=10))
    worker.step()
    worker.step()
    result = result_lines(output)[0]
    assert result["status"] == "FAILED"
    assert result["errorCode"] == "DEADLINE_EXCEEDED"


def test_registry_retention_is_bounded_and_keeps_active_or_recent_duplicates() -> None:
    results = []
    registry = TaskRegistry(results.append, recent_request_limit=2, prompt_deal_limit=2)
    commands = [command("NEW_GAME", f"new-{index}") for index in range(3)]
    registry.submit(commands[0], 0.0)
    registry.submit(commands[0], 0.0)
    assert results[-1]["errorCode"] == "DUPLICATE_REQUEST_ID"
    registry.cancel(commands[0])
    registry.submit(commands[1], 0.0)
    registry.cancel(commands[1])
    registry.submit(commands[1], 0.0)
    assert results[-1]["errorCode"] == "DUPLICATE_REQUEST_ID"
    registry.submit(commands[2], 0.0)
    registry.cancel(commands[2])
    assert registry.recent_request_count == 2
    registry.submit(commands[0], 0.0)
    assert commands[0].request_id in registry.active_request_ids


def test_prompt_state_is_bounded_and_deal_cancel_releases_its_lifecycle() -> None:
    results = []
    registry = TaskRegistry(results.append, recent_request_limit=16, prompt_deal_limit=2)
    facts = SimpleNamespace(
        generation=1,
        lifecycle=SimpleNamespace(stage="preplay", texts=("call", "no_call")),
        hand=HAND17,
        bottom_cards=None,
        observed_at="2026-08-26T00:00:00.000Z",
    )
    for index in range(3):
        prompt = parse_command(
            {
                "contractVersion": "recognition.v1",
                "messageType": "SUBMIT",
                "taskType": "PREPLAY_PROMPT",
                "requestId": f"prompt-{index}",
                "deadlineMs": 1000,
                "dealId": f"deal-{index}",
                "generation": 1,
                "entryMode": "accept_current_stable_prompt",
            }
        )
        registry.submit(prompt, 0.0)
        for _ in range(3):
            registry.observe(facts, lambda: 0.0)
    assert registry.prompt_deal_count == 2
    deal = command("DEAL", "deal-terminal", dealId="deal-2")
    registry.submit(deal, 0.0)
    registry.cancel(deal)
    assert registry.prompt_deal_count == 1
