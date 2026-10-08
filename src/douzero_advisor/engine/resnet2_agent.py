"""安全加载固定来源的 ResNet2 权重，并执行只读策略推理。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import io
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import torch
from torch import nn

from douzero_advisor._vendor.resnet2.models import ResnetModel
from douzero_advisor.engine.resnet2_observation import build_resnet2_observation
from douzero_advisor.model_service.checkpoint_errors import (
    CheckpointIntegrityError,
    CheckpointStructureError,
)


SUPPORTED_POSITIONS = frozenset({"landlord", "landlord_down", "landlord_up"})
TRUSTED_RESNET2_SHA256: Mapping[str, str] = MappingProxyType(
    {
        "landlord": "df11501facc740b10bb5f8ea32e7476b4aa32af9eff4371ffb6c27aef85009ef",
        "landlord_down": "ab356cd313e871f2cc0ad33018e5aa3b2a8b5524e22c5731a6465152c7ff20b7",
        "landlord_up": "a4b502fa1a7a04b2cce34c404e7f6f5c5c6b54e7865c1129b136e1e5aa28e72c",
    }
)
PINNED_UPSTREAM_COMMIT = "85afd773abd01c411f543d6ade5b99a4fde327d2"
PINNED_UPSTREAM_REPOSITORY = "https://github.com/EdwardPooh/douzero-resnet-2.0"
PINNED_LICENSE = "GPL-3.0-only"
PINNED_MODEL_TYPE = "resnet2_card_play"
PINNED_DEVICE_DEFAULT = "cpu"
TRUSTED_RESNET2_FILE_SIZE: Mapping[str, int] = MappingProxyType(
    {
        "landlord": 14_199_345,
        "landlord_down": 14_200_817,
        "landlord_up": 14_200_817,
    }
)


class ResNet2ManifestError(ValueError):
    """Raised when the source-controlled ResNet 2.0 manifest is malformed."""


def _resnet_model_class() -> type[nn.Module]:
    return ResnetModel


class SafeResNet2Agent:
    """Strict, digest-pinned inference wrapper for one ResNet 2.0 position."""

    def __init__(
        self,
        position: str,
        checkpoint_path: str | Path,
        *,
        device: str | torch.device = "cpu",
        observation_builder: Callable[[Any], Mapping[str, Any]] | None = None,
    ) -> None:
        if position not in SUPPORTED_POSITIONS:
            raise ValueError(f"unsupported ResNet 2.0 position: {position}")

        path = Path(checkpoint_path)
        actual_file_size = path.stat().st_size
        expected_file_size = TRUSTED_RESNET2_FILE_SIZE[position]
        if actual_file_size != expected_file_size:
            raise CheckpointIntegrityError(
                f"checkpoint file size mismatch for {path}: expected "
                f"{expected_file_size}, got {actual_file_size}"
            )
        checkpoint_bytes = path.read_bytes()
        actual_sha256 = hashlib.sha256(checkpoint_bytes).hexdigest()
        expected_sha256 = TRUSTED_RESNET2_SHA256[position]
        if actual_sha256 != expected_sha256:
            raise CheckpointIntegrityError(
                f"checkpoint SHA-256 mismatch for {path}: expected "
                f"{expected_sha256}, got {actual_sha256}"
            )

        model = _resnet_model_class()()
        state = torch.load(
            io.BytesIO(checkpoint_bytes),
            map_location="cpu",
            weights_only=True,
        )
        self._validate_state_dict(model, state)
        model.load_state_dict(state, strict=True)
        resolved_device = torch.device(device)
        model.to(resolved_device)
        model.eval()

        self.position = position
        self.checkpoint_path = path
        self.checkpoint_file_size = actual_file_size
        self.checkpoint_sha256 = actual_sha256
        self.device = resolved_device
        self.model = model
        self._observation_builder = observation_builder or build_resnet2_observation

    @classmethod
    def from_manifest(
        cls,
        position: str,
        manifest_path: str | Path,
        *,
        device: str | torch.device = "cpu",
        observation_builder: Callable[[Any], Mapping[str, Any]] | None = None,
    ) -> SafeResNet2Agent:
        manifest_file = Path(manifest_path)
        manifest = cls._load_manifest(manifest_file)
        try:
            model_entry = manifest["models"][position]
        except KeyError as error:
            raise ValueError(f"no trusted ResNet 2.0 checkpoint for position: {position}") from error
        return cls(
            position,
            manifest_file.parent / model_entry["path"],
            device=device,
            observation_builder=observation_builder,
        )

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ResNet2ManifestError(f"ResNet 2.0 manifest does not exist: {path}") from error
        except json.JSONDecodeError as error:
            raise ResNet2ManifestError(f"ResNet 2.0 manifest is invalid JSON: {path}") from error
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ResNet2ManifestError("ResNet 2.0 manifest must use schema_version 1")
        expected_top_level_keys = {
            "schema_version",
            "upstream_repository",
            "upstream_commit",
            "license",
            "device_default",
            "models",
        }
        if set(raw) != expected_top_level_keys:
            missing = sorted(expected_top_level_keys - set(raw))
            unexpected = sorted(set(raw) - expected_top_level_keys)
            raise ResNet2ManifestError(
                "ResNet 2.0 manifest fields do not exactly match: "
                f"missing={missing}, unexpected={unexpected}"
            )
        if raw.get("upstream_repository") != PINNED_UPSTREAM_REPOSITORY:
            raise ResNet2ManifestError("ResNet 2.0 manifest upstream repository is not pinned")
        if raw.get("upstream_commit") != PINNED_UPSTREAM_COMMIT:
            raise ResNet2ManifestError("ResNet 2.0 manifest upstream commit is not pinned")
        if raw.get("license") != PINNED_LICENSE:
            raise ResNet2ManifestError("ResNet 2.0 manifest license is not pinned")
        if raw.get("device_default") != PINNED_DEVICE_DEFAULT:
            raise ResNet2ManifestError("ResNet 2.0 manifest device_default must be cpu")
        models = raw.get("models")
        if not isinstance(models, dict) or set(models) != SUPPORTED_POSITIONS:
            raise ResNet2ManifestError("ResNet 2.0 manifest must contain exactly three positions")
        for position, expected_digest in TRUSTED_RESNET2_SHA256.items():
            entry = models.get(position)
            if not isinstance(entry, dict):
                raise ResNet2ManifestError(f"manifest model entry is invalid: {position}")
            expected_entry_keys = {"path", "sha256", "file_size", "model_type"}
            if set(entry) != expected_entry_keys:
                missing = sorted(expected_entry_keys - set(entry))
                unexpected = sorted(set(entry) - expected_entry_keys)
                raise ResNet2ManifestError(
                    f"manifest model fields do not exactly match for {position}: "
                    f"missing={missing}, unexpected={unexpected}"
                )
            relative_path = entry.get("path")
            if (
                not isinstance(relative_path, str)
                or not relative_path
                or Path(relative_path).name != relative_path
            ):
                raise ResNet2ManifestError(f"manifest model path is unsafe: {position}")
            if entry.get("sha256") != expected_digest:
                raise ResNet2ManifestError(f"manifest digest is not trusted: {position}")
            if entry.get("file_size") != TRUSTED_RESNET2_FILE_SIZE[position]:
                raise ResNet2ManifestError(f"manifest file size is not trusted: {position}")
            if entry.get("model_type") != PINNED_MODEL_TYPE:
                raise ResNet2ManifestError(f"manifest model type is not trusted: {position}")
        return raw

    @staticmethod
    def _validate_state_dict(model: nn.Module, state: Any) -> None:
        if not isinstance(state, Mapping):
            raise CheckpointStructureError("checkpoint object is not a state dict")
        expected = model.state_dict()
        if not all(isinstance(key, str) for key in state) or set(state) != set(expected):
            missing = sorted(set(expected) - set(state))
            unexpected = sorted(set(state) - set(expected), key=str)
            raise CheckpointStructureError(
                "checkpoint keys do not exactly match ResNet 2.0: "
                f"missing={missing}, unexpected={unexpected}"
            )
        for key, expected_tensor in expected.items():
            actual_tensor = state[key]
            if not isinstance(actual_tensor, torch.Tensor):
                raise CheckpointStructureError(f"checkpoint value for {key} is not a tensor")
            if actual_tensor.shape != expected_tensor.shape:
                raise CheckpointStructureError(
                    f"checkpoint tensor shape mismatch for {key}: "
                    f"expected {tuple(expected_tensor.shape)}, got {tuple(actual_tensor.shape)}"
                )

    def act(self, infoset: Any) -> list[int]:
        action, _values = self.act_with_values(infoset)
        return action

    def act_with_values(self, infoset: Any) -> tuple[list[int], tuple[float, ...]]:
        if getattr(infoset, "player_position", None) != self.position:
            raise ValueError(
                f"agent position {self.position} cannot act for "
                f"{getattr(infoset, 'player_position', None)}"
            )
        legal_actions = getattr(infoset, "legal_actions", None)
        return self.act_observation_with_values(
            self._observation_builder(infoset), legal_actions
        )

    def act_observation(
        self,
        observation: Mapping[str, Any],
        legal_actions: Sequence[Sequence[int]] | None,
    ) -> list[int]:
        action, _values = self.act_observation_with_values(observation, legal_actions)
        return action

    def act_observation_with_values(
        self,
        observation: Mapping[str, Any],
        legal_actions: Sequence[Sequence[int]] | None,
    ) -> tuple[list[int], tuple[float, ...]]:
        if not legal_actions:
            raise ValueError("InfoSet has no legal actions")
        if len(legal_actions) == 1:
            return list(legal_actions[0]), (0.0,)

        try:
            z_batch = torch.as_tensor(
                observation["z_batch"], dtype=torch.float32, device=self.device
            )
            x_batch = torch.as_tensor(
                observation["x_batch"], dtype=torch.float32, device=self.device
            )
        except KeyError as error:
            raise ValueError(f"ResNet 2.0 observation is missing {error.args[0]}") from error
        expected_actions = len(legal_actions)
        if tuple(z_batch.shape) != (expected_actions, 40, 54):
            raise ValueError(
                "ResNet 2.0 z_batch shape mismatch: expected "
                f"({expected_actions}, 40, 54), got {tuple(z_batch.shape)}"
            )
        if tuple(x_batch.shape) != (expected_actions, 15):
            raise ValueError(
                "ResNet 2.0 x_batch shape mismatch: expected "
                f"({expected_actions}, 15), got {tuple(x_batch.shape)}"
            )
        if not bool(torch.isfinite(z_batch).all().item()):
            raise ValueError("ResNet 2.0 z_batch contains non-finite values")
        if not bool(torch.isfinite(x_batch).all().item()):
            raise ValueError("ResNet 2.0 x_batch contains non-finite values")
        with torch.inference_mode():
            values = self.model(z_batch, x_batch, return_value=True)["values"]
        if tuple(values.shape) != (expected_actions, 1):
            raise RuntimeError(
                "ResNet 2.0 output shape mismatch: expected "
                f"({expected_actions}, 1), got {tuple(values.shape)}"
            )
        if not bool(torch.isfinite(values).all().item()):
            raise RuntimeError("ResNet 2.0 output contains non-finite values")
        best_action_index = int(torch.argmax(values, dim=0)[0].item())
        return list(legal_actions[best_action_index]), tuple(
            float(value) for value in values[:, 0].tolist()
        )
