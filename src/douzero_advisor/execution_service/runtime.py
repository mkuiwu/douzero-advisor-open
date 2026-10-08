"""执行 Worker 的按需捕获、视觉读取和自动化点击装配。

本模块只组装 capture、reader 和 automation 适配，不维护牌局历史、不调度
识别任务、不加载模型。所有点击能力都由 Java 授权后才触发。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from douzero_advisor.automation.click_scheduler import (
    FinalClickClient,
    ThreadedFinalClickScheduler,
)
from douzero_advisor.automation.hand_selection import HandSelectionExecutor
from douzero_advisor.automation.win32_mouse import Win32ClientMouse
from douzero_advisor.capture.errors import CaptureNotReadyError
from douzero_advisor.capture.wgc_capture import WgcWindowCapture
from douzero_advisor.vision.action_button_reader import (
    ActionButtonRead,
    ActionButtonReader,
)
from douzero_advisor.vision.hand_reader import HandRead
from douzero_advisor.vision.lifecycle_button_reader import (
    LifecycleButtonRead,
    LifecycleButtonReader,
)
from douzero_advisor.vision.live_observation import LiveObservationReader


class ExecutionRuntimeError(ValueError):
    """执行运行时无法建立安全的按需捕获和点击能力。"""


CAPTURE_READY_TIMEOUT_SECONDS = 1.5


@dataclass
class ExecutionRuntime:
    """执行 Worker 运行时持有的按需捕获和自动化能力。

    capture 是 WGC 持续会话，但执行服务只在需要时读取一帧，不做轮询。
    observation_reader 只用于读取本方手牌和动作按钮；lifecycle_button_reader
    读取局前生命周期按钮，不读取对手动作历史。
    """

    capture: WgcWindowCapture
    observation_reader: LiveObservationReader
    action_button_reader: ActionButtonReader
    mouse: Win32ClientMouse
    selection_executor: HandSelectionExecutor
    click_scheduler: ThreadedFinalClickScheduler
    lifecycle_button_reader: LifecycleButtonReader = field(default_factory=LifecycleButtonReader)

    def capture_frame(self) -> np.ndarray:
        """按需读取一帧；超时或窗口丢失时抛出 ExecutionRuntimeError。"""

        deadline = time.monotonic() + CAPTURE_READY_TIMEOUT_SECONDS
        while True:
            try:
                frame = self.capture.capture()
            except CaptureNotReadyError as error:
                if time.monotonic() >= deadline:
                    raise ExecutionRuntimeError(
                        "游戏窗口画面未在 "
                        f"{CAPTURE_READY_TIMEOUT_SECONDS:.1f} 秒内就绪: {error.reason}"
                    ) from error
                # 没有窗口时 capture 会立即返回 waiting_for_game，短暂让出 CPU；
                # stabilizing 情况则等待下一次 WGC 新帧继续累计稳定计数。
                time.sleep(0.05)
                continue
            except Exception as error:
                raise ExecutionRuntimeError(
                    f"无法获取游戏窗口画面: {type(error).__name__}: {error}"
                ) from error
            pixels = getattr(frame, "pixels", None)
            if not isinstance(pixels, np.ndarray):
                raise ExecutionRuntimeError("无法获取有效的游戏窗口画面")
            return pixels

    def read_hand(self, frame: np.ndarray) -> HandRead | None:
        """读取当前本方手牌；不可读时返回 None。"""

        try:
            result = self.observation_reader.read_my_hand_with_diagnostics(frame)
        except Exception:
            return None
        return result.value

    def read_action_buttons(self, frame: np.ndarray) -> ActionButtonRead:
        """读取当前出牌/不出按钮布局。"""

        return self.action_button_reader.read(frame)

    def read_lifecycle_buttons(self, frame: np.ndarray) -> LifecycleButtonRead:
        """读取叫地主、抢地主和加倍按钮布局。"""

        return self.lifecycle_button_reader.read(frame)

    def close(self) -> None:
        """释放 WGC 捕获会话。"""

        try:
            self.capture.close()
        except Exception:
            pass


def build_execution_runtime(manifest_path: str | Path) -> ExecutionRuntime:
    """从实时标定清单组装执行 Worker 运行时。

    复用识别服务的 capture 和 reader 组装逻辑，但只持有按需捕获和
    本方手牌/按钮读取能力，不加载任何识别任务调度器。
    """

    from douzero_advisor.recognition_service.runtime_assembly import (
        build_live_runtime,
    )

    try:
        capture, observation_reader = build_live_runtime(manifest_path)
    except Exception as error:
        raise ExecutionRuntimeError(
            f"无法建立按需捕获能力: {type(error).__name__}: {error}"
        ) from error

    mouse = Win32ClientMouse()

    def capture_hand() -> HandRead | None:
        try:
            frame = capture.capture()
            pixels = getattr(frame, "pixels", None)
            if not isinstance(pixels, np.ndarray):
                return None
            return observation_reader.read_my_hand_with_diagnostics(
                pixels
            ).value
        except Exception:
            return None

    selection_executor = HandSelectionExecutor(
        capture_hand=capture_hand,
        click_client=mouse.click,
        frame_height=819,
        user_intervention_check=mouse.is_user_left_button_pressed,
    )

    click_scheduler = ThreadedFinalClickScheduler(
        FinalClickClientAdapter(mouse)
    )

    return ExecutionRuntime(
        capture=capture,
        observation_reader=observation_reader,
        action_button_reader=ActionButtonReader(),
        lifecycle_button_reader=LifecycleButtonReader(),
        mouse=mouse,
        selection_executor=selection_executor,
        click_scheduler=click_scheduler,
    )


class FinalClickClientAdapter(FinalClickClient):
    """把 Win32ClientMouse 适配为 ThreadedFinalClickScheduler 的最终点击客户端。"""

    def __init__(self, mouse: Win32ClientMouse) -> None:
        self._mouse = mouse

    def __call__(
        self,
        point: tuple[int, int],
        *,
        not_before: float,
        not_after: float,
    ) -> float:
        return self._mouse.click_in_window(
            point,
            not_before=not_before,
            not_after=not_after,
        )
