from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

import cv2
import numpy as np

from douzero_advisor.vision.hand_reader import FRAME_HEIGHT, FRAME_WIDTH

TURN_READY_ROIS: Mapping[str, tuple[int, int, int, int]] = MappingProxyType(
    {
        "left_play": (390, 354, 470, 435),
        "right_play": (988, 354, 1068, 435),
        "my_play": (604, 478, 682, 558),
    }
)

_FEATURE_WIDTH = 96
_FEATURE_HEIGHT = 16
_SHA256_LENGTH = 64
_LOCAL_TURN_HSV_LOWER = (10, 100, 120)
_LOCAL_TURN_HSV_UPPER = (40, 255, 255)
# The calibrated local countdown occupies roughly 3,000 orange pixels; all
# retained non-local-turn frames have fewer than 100.  Leave generous room
# for anti-aliasing, compression, and timer digits while remaining far clear
# of a result-card suit or other small orange decoration.
_LOCAL_TURN_MIN_ORANGE_PIXELS = 1_000


@dataclass(frozen=True, slots=True)
class TurnReadyCalibrationSample:
    """One independently grouped READY or non-READY calibration frame."""

    frame: np.ndarray = field(repr=False, compare=False)
    region: str
    ready: bool
    group: str
    skin: str

    def __post_init__(self) -> None:
        _validate_frame(self.frame)
        if self.region not in TURN_READY_ROIS:
            raise ValueError(f"unknown turn-ready region: {self.region}")
        if type(self.ready) is not bool:
            raise ValueError("turn-ready label must be a bool")
        if not isinstance(self.group, str) or not self.group.strip():
            raise ValueError("turn-ready group must be a non-empty string")
        if not isinstance(self.skin, str) or not self.skin.strip():
            raise ValueError("turn-ready skin must be a non-empty string")
        object.__setattr__(self, "group", self.group.strip())
        object.__setattr__(self, "skin", self.skin.strip())


@dataclass(frozen=True, slots=True)
class TurnReadyCalibration:
    """Per-region grouped thresholds and pre-stacked runtime templates."""

    positive_loso_distance: float
    nearest_negative_distance: float
    maximum_distance: float
    minimum_margin: float
    positive_sample_count: int
    positive_group_count: int
    negative_sample_count: int
    negative_group_count: int
    _positive_features: np.ndarray = field(repr=False, compare=False)
    _negative_features: np.ndarray = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        self._positive_features.setflags(write=False)
        self._negative_features.setflags(write=False)


@dataclass(frozen=True, slots=True)
class TurnReadyDecision:
    """Structured runtime evidence for one region's READY decision."""

    region: str
    decision: str
    enabled: bool
    positive_distance: float | None
    negative_distance: float | None
    maximum_distance: float | None
    minimum_margin: float | None
    observed_margin: float | None
    artifact_sha256: str | None
    config_sha256: str | None

    @property
    def ready(self) -> bool:
        return self.decision == "READY"


class TurnReadyReader:
    """Classify explicit turn-ready pixels with grouped, fail-closed gates."""

    def __init__(
        self,
        calibrations: Mapping[str, TurnReadyCalibration],
        *,
        artifact_sha256: str | None = None,
        config_sha256: str | None = None,
    ) -> None:
        unknown = set(calibrations).difference(TURN_READY_ROIS)
        if unknown:
            raise ValueError(f"unknown turn-ready calibration regions: {sorted(unknown)}")
        self._artifact_sha256 = _optional_sha256(artifact_sha256, "artifact_sha256")
        self._config_sha256 = _optional_sha256(config_sha256, "config_sha256")
        self._calibrations = MappingProxyType(dict(calibrations))
        self._last_diagnostics: dict[str, TurnReadyDecision] = {}

    @property
    def calibrations(self) -> Mapping[str, TurnReadyCalibration]:
        return self._calibrations

    @property
    def diagnostics(self) -> Mapping[str, TurnReadyDecision]:
        return MappingProxyType(dict(self._last_diagnostics))

    @property
    def last_diagnostics(self) -> Mapping[str, TurnReadyDecision]:
        return self.diagnostics

    @classmethod
    def from_labeled_frames(
        cls,
        samples: Sequence[TurnReadyCalibrationSample],
        *,
        artifact_sha256: str | None = None,
        config_sha256: str | None = None,
    ) -> TurnReadyReader:
        by_region: dict[str, list[tuple[TurnReadyCalibrationSample, np.ndarray]]] = defaultdict(
            list
        )
        for sample in samples:
            if not isinstance(sample, TurnReadyCalibrationSample):
                raise ValueError("turn-ready samples must be TurnReadyCalibrationSample values")
            by_region[sample.region].append((sample, _feature(sample.frame, sample.region)))

        calibrations: dict[str, TurnReadyCalibration] = {}
        for region, region_samples in by_region.items():
            calibration = _derive_calibration(region_samples)
            if calibration is not None:
                calibrations[region] = calibration
        return cls(
            calibrations,
            artifact_sha256=artifact_sha256,
            config_sha256=config_sha256,
        )

    def classify(self, frame: np.ndarray, region: str) -> TurnReadyDecision:
        _validate_frame(frame)
        if region not in TURN_READY_ROIS:
            raise ValueError(f"unknown turn-ready region: {region}")
        calibration = self._calibrations.get(region)
        if calibration is None:
            decision = TurnReadyDecision(
                region=region,
                decision="UNKNOWN",
                enabled=False,
                positive_distance=None,
                negative_distance=None,
                maximum_distance=None,
                minimum_margin=None,
                observed_margin=None,
                artifact_sha256=self._artifact_sha256,
                config_sha256=self._config_sha256,
            )
            self._last_diagnostics[region] = decision
            return decision

        observed = _feature(frame, region)
        positive_distance = _nearest_distance(observed, calibration._positive_features)
        negative_distance = _nearest_distance(observed, calibration._negative_features)
        observed_margin = negative_distance - positive_distance
        ready = (
            positive_distance <= calibration.maximum_distance
            and observed_margin >= calibration.minimum_margin
        )
        decision = TurnReadyDecision(
            region=region,
            decision="READY" if ready else "UNKNOWN",
            enabled=True,
            positive_distance=positive_distance,
            negative_distance=negative_distance,
            maximum_distance=calibration.maximum_distance,
            minimum_margin=calibration.minimum_margin,
            observed_margin=observed_margin,
            artifact_sha256=self._artifact_sha256,
            config_sha256=self._config_sha256,
        )
        self._last_diagnostics[region] = decision
        return decision

    def is_ready(self, frame: np.ndarray, region: str) -> bool:
        return self.classify(frame, region).ready


def has_local_turn_indicator(frame: np.ndarray) -> bool:
    """Detect the client's orange local-turn countdown without skin templates."""
    _validate_frame(frame)
    left, top, right, bottom = TURN_READY_ROIS["my_play"]
    hsv = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _LOCAL_TURN_HSV_LOWER, _LOCAL_TURN_HSV_UPPER)
    return int(np.count_nonzero(mask)) >= _LOCAL_TURN_MIN_ORANGE_PIXELS


def _derive_calibration(
    samples: Sequence[tuple[TurnReadyCalibrationSample, np.ndarray]],
) -> TurnReadyCalibration | None:
    positives = [(sample, feature) for sample, feature in samples if sample.ready]
    negatives = [(sample, feature) for sample, feature in samples if not sample.ready]
    if not positives or not negatives:
        return None

    positive_groups_by_skin: dict[str, set[str]] = defaultdict(set)
    for sample, _feature_value in positives:
        positive_groups_by_skin[sample.skin].add(sample.group)
    if any(len(groups) < 2 for groups in positive_groups_by_skin.values()):
        return None

    negative_groups = {sample.group for sample, _feature_value in negatives}
    if len(negative_groups) < 2:
        return None

    positive_loso_distances: list[float] = []
    for query, query_feature in positives:
        other_group_same_skin = np.stack(
            [
                feature
                for candidate, feature in positives
                if candidate.skin == query.skin and candidate.group != query.group
            ]
        )
        positive_loso_distances.append(_nearest_distance(query_feature, other_group_same_skin))

    positive_features = np.ascontiguousarray(
        np.stack([feature for _sample, feature in positives]),
        dtype=np.float32,
    )
    negative_features = np.ascontiguousarray(
        np.stack([feature for _sample, feature in negatives]),
        dtype=np.float32,
    )
    positive_loso_distance = max(positive_loso_distances)
    nearest_negative_distance = min(
        _nearest_distance(feature, positive_features) for _sample, feature in negatives
    )
    if positive_loso_distance >= nearest_negative_distance:
        return None

    maximum_distance = (positive_loso_distance + nearest_negative_distance) / 2.0
    minimum_margin = (nearest_negative_distance - positive_loso_distance) / 2.0

    # Validate every negative with its entire group held out. This prevents an
    # included template from proving safety solely by matching itself.
    for query, query_feature in negatives:
        held_out_negative_features = np.stack(
            [feature for candidate, feature in negatives if candidate.group != query.group]
        )
        positive_distance = _nearest_distance(query_feature, positive_features)
        negative_distance = _nearest_distance(query_feature, held_out_negative_features)
        if (
            positive_distance <= maximum_distance
            and negative_distance - positive_distance >= minimum_margin
        ):
            return None

    return TurnReadyCalibration(
        positive_loso_distance=positive_loso_distance,
        nearest_negative_distance=nearest_negative_distance,
        maximum_distance=maximum_distance,
        minimum_margin=minimum_margin,
        positive_sample_count=len(positives),
        positive_group_count=len({sample.group for sample, _feature_value in positives}),
        negative_sample_count=len(negatives),
        negative_group_count=len(negative_groups),
        _positive_features=positive_features,
        _negative_features=negative_features,
    )


def _feature(frame: np.ndarray, region: str) -> np.ndarray:
    left, top, right, bottom = TURN_READY_ROIS[region]
    crop = frame[top:bottom, left:right]
    resized = cv2.resize(
        crop,
        (_FEATURE_WIDTH, _FEATURE_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )
    return np.ascontiguousarray(resized, dtype=np.float32).reshape(-1) / np.float32(255.0)


def _nearest_distance(feature: np.ndarray, templates: np.ndarray) -> float:
    distances = np.mean(np.abs(templates - feature), axis=1)
    return float(np.min(distances))


def _validate_frame(frame: np.ndarray) -> None:
    if not isinstance(frame, np.ndarray) or frame.shape != (
        FRAME_HEIGHT,
        FRAME_WIDTH,
        3,
    ):
        raise ValueError("frame must be a 1455x819 3-channel BGR image")


def _optional_sha256(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value.lower())
    ):
        raise ValueError(f"{name} must contain 64 hexadecimal characters")
    return value.lower()
