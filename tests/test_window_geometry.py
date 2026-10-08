from __future__ import annotations

from dataclasses import replace

import pytest

from douzero_advisor.capture.window_geometry import (
    WindowGeometryController,
    WindowGeometryError,
    WindowGeometrySnapshot,
)


class FakeGeometryBackend:
    def __init__(
        self,
        *,
        window_rect: tuple[int, int, int, int] = (100, 200, 1275, 861),
        client_rect: tuple[int, int, int, int] = (0, 0, 1161, 654),
        restored_rect: tuple[int, int, int, int] | None = None,
        maximized: bool = False,
        minimized: bool = False,
    ) -> None:
        self._snapshot = WindowGeometrySnapshot(
            window_rect=window_rect,
            client_rect=client_rect,
            dpi=120,
            maximized=maximized,
            minimized=minimized,
        )
        self._restored_rect = restored_rect or window_rect
        self.calls: list[tuple[object, ...]] = []

    def snapshot(self, handle: int) -> WindowGeometrySnapshot:
        self.calls.append(("snapshot", handle))
        return self._snapshot

    def restore(self, handle: int) -> None:
        self.calls.append(("restore", handle))
        self._snapshot = replace(
            self._snapshot,
            window_rect=self._restored_rect,
            maximized=False,
            minimized=False,
        )

    def resize(self, handle: int, width: int, height: int) -> None:
        self.calls.append(("resize", handle, width, height))
        left, top, _right, _bottom = self._snapshot.window_rect
        self._snapshot = replace(
            self._snapshot,
            window_rect=(left, top, left + width, top + height),
        )


def test_resize_adds_wgc_error_to_current_outer_size() -> None:
    backend = FakeGeometryBackend()
    controller = WindowGeometryController(backend)

    snapshot = controller.resize_for_content(101, (1163, 655), (1455, 819))

    assert backend.calls == [
        ("snapshot", 101),
        ("resize", 101, 1467, 825),
        ("snapshot", 101),
    ]
    assert snapshot.window_rect == (100, 200, 1567, 1025)


def test_resize_restores_a_maximized_window_before_measuring() -> None:
    backend = FakeGeometryBackend(
        window_rect=(0, 0, 1920, 1080),
        restored_rect=(372, 238, 1547, 899),
        maximized=True,
    )

    snapshot = WindowGeometryController(backend).resize_for_content(
        101,
        (1163, 655),
        (1455, 819),
    )

    assert backend.calls[:3] == [
        ("snapshot", 101),
        ("restore", 101),
        ("snapshot", 101),
    ]
    assert snapshot.window_rect == (372, 238, 1839, 1063)


def test_resize_restores_a_minimized_window_before_measuring() -> None:
    backend = FakeGeometryBackend(
        window_rect=(0, 0, 1920, 1080),
        restored_rect=(372, 238, 1547, 899),
        client_rect=(0, 0, 0, 0),
        minimized=True,
    )

    snapshot = WindowGeometryController(backend).resize_for_content(
        101,
        (1163, 655),
        (1455, 819),
    )

    assert backend.calls[:3] == [
        ("snapshot", 101),
        ("restore", 101),
        ("snapshot", 101),
    ]
    assert snapshot.window_rect == (372, 238, 1839, 1063)


@pytest.mark.parametrize(
    ("handle", "current_size", "target_size"),
    [
        (0, (1163, 655), (1455, 819)),
        (-1, (1163, 655), (1455, 819)),
        (True, (1163, 655), (1455, 819)),
        (101, (0, 655), (1455, 819)),
        (101, (1163, 655), (1455, 0)),
    ],
)
def test_resize_rejects_invalid_handles_and_sizes(
    handle: object,
    current_size: tuple[int, int],
    target_size: tuple[int, int],
) -> None:
    controller = WindowGeometryController(FakeGeometryBackend())

    with pytest.raises(WindowGeometryError):
        controller.resize_for_content(handle, current_size, target_size)  # type: ignore[arg-type]


def test_resize_rejects_a_nonpositive_corrected_outer_size() -> None:
    backend = FakeGeometryBackend(window_rect=(10, 20, 20, 30))

    with pytest.raises(WindowGeometryError, match="corrected outer size"):
        WindowGeometryController(backend).resize_for_content(
            101,
            (100, 100),
            (1, 1),
        )

    assert backend.calls == [("snapshot", 101)]
