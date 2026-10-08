from __future__ import annotations

from typing import Any

import douzero_advisor.recognition_service.runtime_assembly as runtime


# 验证生产 CV 以 WGC 实测物理尺寸为准，并在用户启动服务后注入有界窗口收敛控制器。
def test_live_runtime_uses_physical_dpi_and_startup_geometry_recovery(monkeypatch) -> None:
    calls: list[str] = []
    captured_kwargs: dict[str, Any] = {}
    manifest = {"capture": {"width": 1455, "height": 819}}

    monkeypatch.setattr(runtime, "enable_per_monitor_dpi_awareness", lambda: calls.append("dpi"))
    monkeypatch.setattr(runtime, "_load_manifest", lambda _path: manifest)
    monkeypatch.setattr(runtime, "_build_reader", lambda _manifest, *_paths: "reader")
    monkeypatch.setattr(runtime, "PyWin32WindowFinder", lambda: "finder")
    monkeypatch.setattr(runtime, "PyWin32WindowGeometry", lambda: "geometry_backend")
    monkeypatch.setattr(
        runtime,
        "WindowGeometryController",
        lambda backend: ("geometry_controller", backend),
    )

    def build_capture(*args: object, **kwargs: Any) -> str:
        calls.append("capture")
        captured_kwargs.update(kwargs)
        assert args == ("finder",)
        return "capture"

    monkeypatch.setattr(runtime, "WgcWindowCapture", build_capture)

    capture, reader = runtime.build_live_runtime("manifest.json")

    assert (capture, reader) == ("capture", "reader")
    assert calls == ["dpi", "capture"]
    assert captured_kwargs["geometry_controller"] == ("geometry_controller", "geometry_backend")
