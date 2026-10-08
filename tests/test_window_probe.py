from __future__ import annotations

from douzero_advisor.capture.window_geometry import WindowGeometrySnapshot
from douzero_advisor.capture.window_probe import GameWindowGeometryService, game_window_available


class Finder:
    def __init__(self, handle: object) -> None:
        self.handle = handle
        self.classes: list[str] = []

    def find_window(self, class_name: str):
        self.classes.append(class_name)
        if isinstance(self.handle, Exception):
            raise self.handle
        return self.handle


# 验证仅正数原生句柄可被视为真实游戏窗口，避免把 None 或虚假值交给 Win32。
def test_game_window_probe_requires_a_positive_native_handle() -> None:
    found = Finder(123)
    missing = Finder(None)
    assert game_window_available(found)
    assert not game_window_available(missing)
    assert found.classes == ["UnityWndClass"]


# 验证窗口查询后端异常时保持“未找到”，不能把错误误报为可启动状态。
def test_game_window_probe_fails_closed_on_backend_errors() -> None:
    assert not game_window_available(Finder(RuntimeError("win32 unavailable")))


class Geometry:
    def __init__(self, size: tuple[int, int], *, dpi: int = 96) -> None:
        self.size = size
        self.dpi = dpi
        self.resize_calls: list[tuple[int, int, int]] = []

    def snapshot(self, _handle: int) -> WindowGeometrySnapshot:
        width, height = self.size
        return WindowGeometrySnapshot(
            (0, 0, width + 20, height + 40), (0, 0, width, height), self.dpi, False
        )

    def restore(self, _handle: int) -> None:
        pass

    def resize(self, handle: int, width: int, height: int) -> None:
        self.resize_calls.append((handle, width, height))
        self.size = (width - 20, height - 40)


# 验证检查路径只报告真实物理尺寸不符，不会在页面检测时擅自修改游戏窗口。
def test_geometry_service_reports_a_true_physical_size_mismatch_without_resizing() -> None:
    finder = Finder(123)
    geometry = Geometry((1280, 720))
    service = GameWindowGeometryService(finder, geometry)

    mismatch = service.inspect()
    assert mismatch.found
    assert mismatch.current_size == (1280, 720)
    assert not mismatch.matches
    assert mismatch.match_mode == "mismatch"
    assert geometry.resize_calls == []


# 验证显式调整路径会根据实际客户区误差校正窗口；检查本身不会改变游戏窗口。
def test_geometry_service_adjusts_only_after_an_explicit_adjust_request() -> None:
    finder = Finder(123)
    geometry = Geometry((1280, 720))
    service = GameWindowGeometryService(finder, geometry)

    service.inspect()
    assert geometry.resize_calls == []

    adjusted = service.adjust()

    assert adjusted.matches
    assert adjusted.current_size == (1455, 819)
    assert geometry.resize_calls == [(123, 1475, 859)]


# 验证 WGC 报出 1457×821 时不再被界面容差误判为匹配，启动期控制器可精确收敛。
def test_geometry_service_corrects_the_two_pixel_wgc_mismatch() -> None:
    finder = Finder(123)
    geometry = Geometry((1457, 821))
    service = GameWindowGeometryService(finder, geometry)

    before = service.inspect()
    adjusted = service.adjust()

    assert not before.matches
    assert before.match_mode == "mismatch"
    assert adjusted.matches
    assert adjusted.current_size == (1455, 819)
    assert geometry.resize_calls == [(123, 1475, 859)]


# 验证 125% 缩放下被虚拟化的逻辑客户区可按 DPI 换算为精确标定物理尺寸。
def test_geometry_service_accepts_125_percent_virtualized_client_size() -> None:
    finder = Finder(123)
    service = GameWindowGeometryService(finder, Geometry((1164, 655), dpi=120))

    status = service.inspect()

    assert status.current_size == (1164, 655)
    assert status.dpi == 120
    assert status.effective_size == (1455, 819)
    assert status.match_mode == "dpi_virtualized"
    assert status.matches


# 验证 150% 缩放使用同一 DPI 比例公式，不依赖预先枚举的固定缩放档位。
def test_geometry_service_accepts_150_percent_virtualized_client_size() -> None:
    finder = Finder(123)
    service = GameWindowGeometryService(finder, Geometry((970, 546), dpi=144))

    status = service.inspect()

    assert status.current_size == (970, 546)
    assert status.dpi == 144
    assert status.effective_size == (1455, 819)
    assert status.match_mode == "dpi_virtualized"
    assert status.matches
