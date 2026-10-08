"""视觉捕获边界共享的错误类型。"""

from __future__ import annotations

from typing import Any


class CaptureError(RuntimeError):
    """只读捕获无法产生可信视觉证据。"""


class CaptureNotReadyError(CaptureError):
    """捕获链路正常但窗口或几何闸门尚未就绪，属于等待状态而非故障。"""

    def __init__(self, status: Any, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class CaptureSizeError(CaptureError):
    """捕获客户区尺寸与标定尺寸不一致。"""


class BlackFrameError(CaptureError):
    """捕获画面接近全黑，不能作为识别证据。"""


class LowContrastFrameError(CaptureError):
    """捕获画面对比度过低，不能作为识别证据。"""
