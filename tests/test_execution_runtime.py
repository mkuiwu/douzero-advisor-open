from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from douzero_advisor.capture.errors import CaptureNotReadyError
from douzero_advisor.execution_service import runtime as execution_runtime
from douzero_advisor.execution_service.runtime import ExecutionRuntime


# 验证执行服务复用 WGC 的 capture 接口，并把其 Frame.pixels 提供给选牌流程。
def test_execution_runtime_reads_pixels_via_wgc_capture() -> None:
    pixels = np.zeros((819, 1455, 3), dtype=np.uint8)

    class Capture:
        def __init__(self) -> None:
            self.calls = 0

        def capture(self) -> SimpleNamespace:
            self.calls += 1
            return SimpleNamespace(pixels=pixels)

    capture = Capture()
    runtime = ExecutionRuntime(
        capture=capture,  # type: ignore[arg-type]
        observation_reader=None,  # type: ignore[arg-type]
        action_button_reader=None,  # type: ignore[arg-type]
        mouse=None,  # type: ignore[arg-type]
        selection_executor=None,  # type: ignore[arg-type]
        click_scheduler=None,  # type: ignore[arg-type]
    )

    assert runtime.capture_frame() is pixels
    assert capture.calls == 1


# 验证执行 Worker 启动后 WGC 尚在稳定尺寸时，会等到可用帧而不是把首帧预热当作选牌失败。
def test_execution_runtime_retries_wgc_stabilization(monkeypatch) -> None:
    pixels = np.zeros((819, 1455, 3), dtype=np.uint8)

    class Capture:
        def __init__(self) -> None:
            self.calls = 0

        def capture(self) -> SimpleNamespace:
            self.calls += 1
            if self.calls == 1:
                raise CaptureNotReadyError(
                    SimpleNamespace(stable_frames=1, required_stable_frames=5),
                    "stabilizing_1_of_5",
                )
            return SimpleNamespace(pixels=pixels)

    monkeypatch.setattr(execution_runtime.time, "sleep", lambda _seconds: None)
    capture = Capture()
    runtime = ExecutionRuntime(
        capture=capture,  # type: ignore[arg-type]
        observation_reader=None,  # type: ignore[arg-type]
        action_button_reader=None,  # type: ignore[arg-type]
        mouse=None,  # type: ignore[arg-type]
        selection_executor=None,  # type: ignore[arg-type]
        click_scheduler=None,  # type: ignore[arg-type]
    )

    assert runtime.capture_frame() is pixels
    assert capture.calls == 2
