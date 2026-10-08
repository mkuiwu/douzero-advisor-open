from __future__ import annotations

import pytest

from douzero_advisor.automation.win32_mouse import (
    ClientClickError,
    CtypesWin32MouseBackend,
    Win32ClientMouse,
)


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _User32:
    def __init__(self, *, clock: _Clock, cursor_advance: float = 0.0) -> None:
        self.clock = clock
        self.cursor_advance = cursor_advance
        self.cursor_positions: list[tuple[int, int]] = []
        self.mouse_events: list[int] = []

    def SetCursorPos(self, x: int, y: int) -> bool:
        self.cursor_positions.append((x, y))
        self.clock.now += self.cursor_advance
        return True

    def mouse_event(self, event: int, *_args: int) -> None:
        self.mouse_events.append(event)


class _Backend:
    def __init__(
        self,
        *,
        hwnd: int = 123,
        client_size: tuple[int, int] = (1455, 819),
        screen_offset: tuple[int, int] = (80, 120),
        window_rect: tuple[int, int, int, int] | None = None,
        window_at_point: int | None = None,
    ) -> None:
        self.hwnd = hwnd
        self.client_size_value = client_size
        self.screen_offset = screen_offset
        self.window_rect_value = window_rect or (
            screen_offset[0],
            screen_offset[1],
            screen_offset[0] + client_size[0],
            screen_offset[1] + client_size[1],
        )
        self.window_at_point = hwnd if window_at_point is None else window_at_point
        self.window_classes: list[str] = []
        self.clicked: list[tuple[int, int]] = []
        self.moved: list[tuple[int, int]] = []
        self.click_holds: list[float | None] = []
        self.window_clicks: list[tuple[tuple[int, int], float, float]] = []
        self.mouse_down_at = 10.7

    def find_window(self, window_class: str) -> int:
        self.window_classes.append(window_class)
        return self.hwnd

    def client_size(self, hwnd: int) -> tuple[int, int]:
        assert hwnd == self.hwnd
        return self.client_size_value

    def client_to_screen(self, hwnd: int, point: tuple[int, int]) -> tuple[int, int]:
        assert hwnd == self.hwnd
        return point[0] + self.screen_offset[0], point[1] + self.screen_offset[1]

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        assert hwnd == self.hwnd
        return self.window_rect_value

    def window_at(self, _screen_point: tuple[int, int]) -> int:
        return self.window_at_point

    def left_click(
        self,
        screen_point: tuple[int, int],
        hold_seconds: float | None = None,
    ) -> None:
        self.clicked.append(screen_point)
        self.click_holds.append(hold_seconds)

    def move(self, screen_point: tuple[int, int]) -> None:
        self.moved.append(screen_point)

    def left_click_in_window(
        self,
        screen_point: tuple[int, int],
        hold_seconds: float,
        *,
        not_before: float,
        not_after: float,
    ) -> float:
        self.window_clicks.append((screen_point, not_before, not_after))
        self.click_holds.append(hold_seconds)
        return self.mouse_down_at


def test_click_resolves_unity_window_and_converts_client_coordinates() -> None:
    backend = _Backend()
    mouse = Win32ClientMouse(backend=backend)

    mouse.click((575, 720))

    assert backend.window_classes == ["UnityWndClass"]
    assert backend.clicked == [(655, 840)]
    assert backend.click_holds == [0.06]


def test_click_scales_capture_coordinates_into_a_slightly_smaller_client() -> None:
    backend = _Backend(
        client_size=(1453, 817),
        screen_offset=(80, 120),
        window_rect=(73, 116, 1540, 941),
    )
    mouse = Win32ClientMouse(backend=backend)

    mouse.click((575, 720))

    assert backend.clicked == [(654, 838)]


@pytest.mark.parametrize(
    ("backend", "point", "message"),
    (
        (_Backend(hwnd=0), (575, 720), "window was not found"),
        (_Backend(client_size=(1400, 819)), (575, 720), "client size changed"),
        (_Backend(), (1455, 720), "outside the client area"),
        (_Backend(window_at_point=456), (575, 720), "covered by another window"),
    ),
)
def test_click_fails_closed_before_mouse_input(
    backend: _Backend,
    point: tuple[int, int],
    message: str,
) -> None:
    mouse = Win32ClientMouse(backend=backend)

    with pytest.raises(ClientClickError, match=message):
        mouse.click(point)

    assert backend.clicked == []


def test_click_reacquires_the_window_for_each_card() -> None:
    backend = _Backend()
    mouse = Win32ClientMouse(backend=backend)

    mouse.click((575, 720))
    mouse.click((525, 720))

    assert backend.window_classes == ["UnityWndClass", "UnityWndClass"]
    assert backend.clicked == [(655, 840), (605, 840)]


def test_park_moves_cursor_to_the_calibrated_centre_safe_area_without_clicking() -> None:
    """识别前归位只移动光标到按钮下方、手牌上方的中部安全区，不能产生鼠标按键事件。"""

    backend = _Backend()
    mouse = Win32ClientMouse(backend=backend)

    mouse.park()

    assert backend.moved == [(810, 695)]
    assert backend.clicked == []
    assert backend.window_clicks == []


def test_park_after_click_waits_fifty_milliseconds_before_moving() -> None:
    """实际点击释放后必须先留出 50ms UI 响应窗口，不能立即移动鼠标破坏点击确认。"""

    backend = _Backend()
    waits: list[float] = []
    mouse = Win32ClientMouse(backend=backend, sleep=waits.append)

    mouse.park_after_click()

    assert waits == [0.05]
    assert backend.moved == [(810, 695)]
    assert backend.clicked == []


def test_move_after_click_waits_then_hovers_requested_client_point() -> None:
    """选牌释放后必须等 50ms，再仅移动到出牌按钮中心且不触发点击。"""

    backend = _Backend()
    waits: list[float] = []
    mouse = Win32ClientMouse(backend=backend, sleep=waits.append)

    mouse.move_after_click((575, 600))

    assert waits == [0.05]
    assert backend.moved == [(655, 720)]
    assert backend.clicked == []


def test_click_in_window_runs_normal_preflight_and_returns_mouse_down_time() -> None:
    backend = _Backend()
    mouse = Win32ClientMouse(backend=backend)

    mouse_down_at = mouse.click_in_window(
        (575, 720), not_before=10.6, not_after=10.8
    )

    assert mouse_down_at == 10.7
    assert backend.window_classes == ["UnityWndClass"]
    assert backend.window_clicks == [((655, 840), 10.6, 10.8)]
    assert backend.click_holds == [0.06]


def _timed_backend(
    clock: _Clock, *, cursor_advance: float = 0.0
) -> tuple[CtypesWin32MouseBackend, _User32]:
    backend = object.__new__(CtypesWin32MouseBackend)
    user32 = _User32(clock=clock, cursor_advance=cursor_advance)
    backend._clock = clock
    backend._user32 = user32
    return backend, user32


@pytest.mark.parametrize("mouse_down_at", [10.6, 10.8])
def test_backend_allows_mouse_down_at_inclusive_window_boundaries(
    mouse_down_at: float,
) -> None:
    clock = _Clock(mouse_down_at)
    backend, user32 = _timed_backend(clock)

    invoked_at = backend.left_click_in_window(
        (655, 840), 0.0, not_before=10.6, not_after=10.8
    )

    assert invoked_at == mouse_down_at
    assert user32.cursor_positions == [(655, 840)]
    assert user32.mouse_events == [backend._LEFT_DOWN, backend._LEFT_UP]


@pytest.mark.parametrize("mouse_down_at", [10.599, 10.801])
def test_backend_rejects_mouse_down_outside_window_without_mouse_event(
    mouse_down_at: float,
) -> None:
    clock = _Clock(mouse_down_at)
    backend, user32 = _timed_backend(clock)

    with pytest.raises(ClientClickError, match="mouse-down would occur"):
        backend.left_click_in_window(
            (655, 840), 0.0, not_before=10.6, not_after=10.8
        )

    assert user32.cursor_positions == [(655, 840)]
    assert user32.mouse_events == []


def test_backend_rechecks_clock_after_cursor_preflight_and_never_mouse_downs_late() -> None:
    clock = _Clock(10.7)
    backend, user32 = _timed_backend(clock, cursor_advance=0.101)

    with pytest.raises(ClientClickError, match="after the click window"):
        backend.left_click_in_window(
            (655, 840), 0.0, not_before=10.6, not_after=10.8
        )

    assert clock.now == pytest.approx(10.801)
    assert user32.cursor_positions == [(655, 840)]
    assert user32.mouse_events == []
