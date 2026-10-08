"""游戏窗口识别的公共常量和只读查找能力。"""

from __future__ import annotations

from typing import Protocol

from douzero_advisor.capture.errors import CaptureError


# 腾讯欢乐斗地主 Unity 客户端的固定窗口类名；窗口句柄只用于只读捕获和几何测量。
WINDOW_CLASS = "UnityWndClass"


class WindowFinder(Protocol):
    """按窗口类名查找目标窗口的最小只读能力。"""

    def find_window(self, class_name: str) -> int | None: ...


class PyWin32WindowFinder:
    """使用 pywin32 查找窗口，不包含截图、聚焦或输入操作。"""

    def find_window(self, class_name: str) -> int | None:
        try:
            import win32gui
        except ImportError as error:
            raise CaptureError("pywin32 is required for Windows window lookup") from error
        handle = win32gui.FindWindow(class_name, None)
        return handle or None
