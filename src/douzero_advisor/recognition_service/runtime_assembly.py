"""实时运行时的证据、诊断和生命周期编排。

运行时把 capture、识别、状态推进和输出记录为可回放事件；日志中的
``unknown`` 表示没有证据，不表示失败或人工补填。这里不改变识别结果，
只负责保留上下文并将不确定边界传给操作者。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from douzero_advisor.capture.dpi_awareness import enable_per_monitor_dpi_awareness
from douzero_advisor.capture.wgc_capture import WgcWindowCapture
from douzero_advisor.capture.window import PyWin32WindowFinder
from douzero_advisor.capture.window_geometry import PyWin32WindowGeometry, WindowGeometryController
from douzero_advisor.vision.action_result_reader import ActionResultReader
from douzero_advisor.vision.hand_reader import HandReader, HandTemplateCatalog
from douzero_advisor.vision.live_markers import PassMarkerCatalog, PassMarkerReader
from douzero_advisor.vision.live_observation import LiveObservationReader
from douzero_advisor.vision.table_reader import (
    JokerWordCatalog,
    TableReader,
    TableTemplateCatalog,
)
from douzero_advisor.vision.three_cards_reader import (
    BottomTemplateCatalog,
    ThreeCardsReader,
)
from douzero_advisor.vision.turn_ready_reader import TurnReadyCalibrationSample, TurnReadyReader

class LiveManifestError(ValueError):
    """The local calibration manifest cannot build a fail-closed runtime."""


def build_live_runtime(
    manifest_path: str | Path,
    *,
    stable_size_frames: int = 5,
    geometry_timeout_seconds: float = 5.0,
):
    # CV 服务必须先使用物理像素坐标，避免 Windows 125%/150% 缩放把客户区虚拟化后
    # 错当成真实 WGC 尺寸。用户按下启动后，首个 WGC 实测尺寸不符时仅在有界次数内
    # 校正游戏窗口；会话会重建并等待精确稳定帧，绝不缩放图像或带着错位 ROI 继续识别。
    enable_per_monitor_dpi_awareness()
    manifest = _load_manifest(manifest_path)
    capture_spec = _mapping(manifest, "capture")
    expected_size = (
        _positive_int(capture_spec, "width"),
        _positive_int(capture_spec, "height"),
    )
    if expected_size != (1455, 819):
        raise LiveManifestError("live capture must use the calibrated 1455x819 size")
    capture = WgcWindowCapture(
        PyWin32WindowFinder(),
        expected_size=expected_size,
        geometry_controller=WindowGeometryController(PyWin32WindowGeometry()),
        stable_size_frames=stable_size_frames,
        geometry_timeout_seconds=geometry_timeout_seconds,
    )
    return capture, _build_reader(manifest, Path(manifest_path))


def build_live_observation_reader(manifest_path: str | Path) -> LiveObservationReader:
    path = Path(manifest_path)
    return _build_reader(_load_manifest(path), path)


def _build_reader(manifest: dict[str, Any], manifest_path: Path) -> LiveObservationReader:
    capture_spec = _mapping(manifest, "capture")
    if (
        _positive_int(capture_spec, "width"),
        _positive_int(capture_spec, "height"),
    ) != (1455, 819):
        raise LiveManifestError("live capture must use the calibrated 1455x819 size")
    root = _data_root(manifest, "capture_root", manifest_path)

    hand_samples = [
        (
            _read_frame(root, item),
            tuple(_string_list(item, "cards")),
        )
        for item in _mapping_list(manifest, "hand_calibration")
    ]
    hand_catalog = HandTemplateCatalog.from_labeled_frames(hand_samples)

    bottom_samples = [
        (
            _read_bottom_calibration(root, item),
            tuple(_string_list(item, "cards")),
        )
        for item in _mapping_list(manifest, "three_cards_calibration")
    ]
    bottom_catalog = BottomTemplateCatalog.from_labeled_frames(bottom_samples)

    table_catalog = TableTemplateCatalog.from_labeled_regions(
        [
            (
                _read_frame(root, item),
                _string(item, "region"),
                tuple(_string_list(item, "cards")),
            )
            for item in _mapping_list(manifest, "table_calibration")
        ]
    )
    joker_word_catalog = JokerWordCatalog.from_labeled_frames(
        [
            _read_joker_word_calibration(root, item)
            for item in _mapping_list(manifest, "joker_word_calibration")
        ]
    )
    pass_catalog = PassMarkerCatalog.from_labeled_frames(
        [
            (
                _read_frame(root, item),
                _string(item, "marker"),
                _box(item),
            )
            for item in _mapping_list(manifest, "pass_marker_calibration")
        ]
    )
    pass_reader = PassMarkerReader(pass_catalog)
    return LiveObservationReader(
        hand_reader=HandReader(hand_catalog),
        tracker_reader=None,
        table_reader=TableReader(hand_catalog, table_catalog, joker_word_catalog),
        three_cards_reader=ThreeCardsReader(bottom_catalog),
        # The live path deliberately reads only pass markers.  Role-badge
        # matching was unstable and is not a role-authority signal; bootstrap
        # uses hand size and the landlord's first play instead.
        marker_reader=pass_reader,
        action_result_reader=ActionResultReader(pass_reader, joker_word_catalog),
        turn_ready_reader=_build_turn_ready_reader(manifest, manifest_path),
        observe_action_cards=True,
    )


def _build_turn_ready_reader(
    manifest: dict[str, Any],
    manifest_path: Path,
) -> TurnReadyReader | None:
    """Build the optional local-control affordance gate.

    Older manifests intentionally omit this calibration.  In that case the
    live listener remains operational but cannot visually delay advice.
    """
    fields = (
        "turn_ready_root",
        "turn_ready_artifact_sha256",
        "turn_ready_calibration",
    )
    present = [field in manifest for field in fields]
    if not any(present):
        return None
    if not all(present):
        raise LiveManifestError("turn-ready calibration fields must be provided together")
    root = _data_root(manifest, "turn_ready_root", manifest_path)
    artifact_sha256 = _sha256(manifest, "turn_ready_artifact_sha256")
    samples = [
        TurnReadyCalibrationSample(
            frame=_read_frame(root, item),
            region=_string(item, "region"),
            ready=_bool(item, "ready"),
            group=_string(item, "group"),
            skin=_string(item, "skin"),
        )
        for item in _mapping_list(manifest, "turn_ready_calibration")
    ]
    reader = TurnReadyReader.from_labeled_frames(
        samples,
        artifact_sha256=artifact_sha256,
    )
    if "my_play" not in reader.calibrations:
        raise LiveManifestError("turn-ready calibration must include a usable my_play region")
    return reader


def _data_root(manifest: dict[str, Any], key: str, manifest_path: Path) -> Path:
    """解析标定根目录：相对路径相对仓库根，不依赖外部 spikes。"""

    raw = Path(_string(manifest, key))
    if raw.is_absolute():
        root = raw
    else:
        root = _repository_root(manifest_path) / raw
    resolved = root.resolve()
    if not resolved.is_dir():
        raise LiveManifestError(f"{key} does not exist: {resolved}")
    return resolved


def _repository_root(start: Path) -> Path:
    for candidate in (start.resolve(), *start.resolve().parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "AGENTS.md").is_file():
            return candidate
    return Path.cwd()


def _load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LiveManifestError(f"live calibration manifest does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise LiveManifestError(f"live calibration manifest is invalid JSON: {path}") from error
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise LiveManifestError("live calibration manifest must use version 1")
    return raw


def _read_frame(root: Path, item: dict[str, Any]) -> np.ndarray:
    path = root / _string(item, "path")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if frame is None:
        raise LiveManifestError(f"calibration frame is missing or unreadable: {path}")
    if frame.shape != (819, 1455, 3):
        raise LiveManifestError(f"calibration frame is not 1455x819 BGR: {path}")
    return frame


def _read_bottom_calibration(root: Path, item: dict[str, Any]) -> np.ndarray:
    split = _string(item, "split")
    if split != "development":
        raise LiveManifestError("bottom templates must come from the development split")
    relative_path = _string(item, "path")
    session_id = _string(item, "session_id")
    if session_id not in Path(relative_path).parts:
        raise LiveManifestError("bottom calibration session_id must match its path")
    expected_hash = _string(item, "sha256").lower()
    if len(expected_hash) != 64 or any(
        character not in "0123456789abcdef" for character in expected_hash
    ):
        raise LiveManifestError("bottom calibration sha256 must contain 64 hexadecimal characters")
    frame = _read_frame(root, item)
    actual_hash = hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()
    if actual_hash != expected_hash:
        raise LiveManifestError(f"bottom calibration frame hash mismatch: {root / relative_path}")
    return frame


def _read_joker_word_calibration(
    root: Path,
    item: dict[str, Any],
) -> tuple[np.ndarray, str, int, int, str, tuple[int, int]]:
    """读取可追溯的 JOKER 五字母模板，拒绝无标签或被替换的素材。"""
    frame = _read_frame(root, item)
    expected_hash = _sha256(item, "sha256")
    actual_hash = hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()
    if actual_hash != expected_hash:
        raise LiveManifestError(f"joker word calibration frame hash mismatch: {root / _string(item, 'path')}")
    rank = _string(item, "rank")
    foreground = _string(item, "foreground")
    if rank not in {"D", "X"} or foreground not in {"red", "dark"}:
        raise LiveManifestError("joker word calibration must declare D/X and red/dark")
    strip_values = _integer_list(item, "strip")
    if len(strip_values) != 2 or not 0 <= strip_values[0] < strip_values[1] <= 32:
        raise LiveManifestError("joker word calibration strip must lie within the rank corner")
    return (
        frame,
        rank,
        _nonnegative_int(item, "left"),
        _nonnegative_int(item, "top"),
        foreground,
        (strip_values[0], strip_values[1]),
    )


def _mapping(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    if not isinstance(value, dict):
        raise LiveManifestError(f"manifest field {key!r} must be an object")
    return value


def _mapping_list(source: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = source.get(key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, dict) for item in value)
    ):
        raise LiveManifestError(f"manifest field {key!r} must be a non-empty object list")
    return value


def _string(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise LiveManifestError(f"manifest field {key!r} must be a non-empty string")
    return value


def _bool(source: dict[str, Any], key: str) -> bool:
    value = source.get(key)
    if type(value) is not bool:
        raise LiveManifestError(f"manifest field {key!r} must be a bool")
    return value


def _sha256(source: dict[str, Any], key: str) -> str:
    value = _string(source, key).lower()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise LiveManifestError(f"manifest field {key!r} must contain 64 hexadecimal characters")
    return value


def _positive_int(source: dict[str, Any], key: str) -> int:
    value = source.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise LiveManifestError(f"manifest field {key!r} must be a positive integer")
    return value


def _nonnegative_int(source: dict[str, Any], key: str) -> int:
    value = source.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LiveManifestError(f"manifest field {key!r} must be a non-negative integer")
    return value


def _string_list(source: dict[str, Any], key: str) -> list[str]:
    value = source.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise LiveManifestError(f"manifest field {key!r} must be a non-empty string list")
    return value


def _integer_list(source: dict[str, Any], key: str) -> list[int]:
    value = source.get(key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        raise LiveManifestError(f"manifest field {key!r} must be an integer list")
    return value


def _box(source: dict[str, Any]) -> tuple[int, int, int, int]:
    values = _integer_list(source, "box")
    if len(values) != 4:
        raise LiveManifestError("marker box must contain four integers")
    return tuple(values)  # type: ignore[return-value]
