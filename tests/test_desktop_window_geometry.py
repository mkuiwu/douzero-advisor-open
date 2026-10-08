from __future__ import annotations

import json

from douzero_advisor.capture.window_probe import GameWindowGeometry
from scripts.desktop_window_geometry import execute, geometry_payload, main


class FakeGeometryService:
    """为桌面桥接测试提供可计数的检查和显式调整结果。"""

    def __init__(self) -> None:
        self.inspect_calls = 0
        self.adjust_calls = 0

    def inspect(self) -> GameWindowGeometry:
        self.inspect_calls += 1
        return GameWindowGeometry(True, (1280, 720))

    def adjust(self) -> GameWindowGeometry:
        self.adjust_calls += 1
        return GameWindowGeometry(True, (1455, 819))

# 验证 Electron 得到的字段名、像素单位和 matches 语义与 Python 窗口状态一致。
def test_geometry_payload_uses_stable_electron_fields() -> None:
    payload = geometry_payload(GameWindowGeometry(True, (1455, 819)))

    assert payload == {
        "found": True,
        "currentSize": [1455, 819],
        "targetSize": [1455, 819],
        "effectiveSize": [1455, 819],
        "dpi": None,
        "matchMode": "physical_pixels",
        "error": None,
        "actionableError": False,
        "matches": True,
    }


# 验证桌面桥接只接受固定检查和显式调整动作，不能借动作字符串调用任意方法。
def test_execute_allows_only_controlled_geometry_actions() -> None:
    service = FakeGeometryService()

    assert execute("inspect", service).current_size == (1280, 720)
    assert service.inspect_calls == 1
    assert execute("adjust", service).current_size == (1455, 819)
    assert service.adjust_calls == 1
    try:
        execute("anything_else", service)
    except ValueError as error:
        assert str(error) == "unsupported geometry action: anything_else"
    else:
        raise AssertionError("unexpected geometry action must be rejected")


# 验证 CLI stdout 只包含一行可解析 JSON，供 Electron 主进程安全读取。
def test_main_prints_one_json_geometry_result(capsys) -> None:
    service = FakeGeometryService()

    assert main(["inspect"], service=service) == 0

    output = capsys.readouterr().out
    assert json.loads(output) == {
        "found": True,
        "currentSize": [1280, 720],
        "targetSize": [1455, 819],
        "effectiveSize": [1280, 720],
        "dpi": None,
        "matchMode": "mismatch",
        "error": None,
        "actionableError": False,
        "matches": False,
    }
