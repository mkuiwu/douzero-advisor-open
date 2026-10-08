from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import torch
from torch import nn

from douzero_advisor.engine.resnet2_agent import (
    PINNED_DEVICE_DEFAULT,
    PINNED_LICENSE,
    PINNED_MODEL_TYPE,
    PINNED_UPSTREAM_COMMIT,
    PINNED_UPSTREAM_REPOSITORY,
    SUPPORTED_POSITIONS,
    TRUSTED_RESNET2_SHA256,
    TRUSTED_RESNET2_FILE_SIZE,
    ResNet2ManifestError,
    SafeResNet2Agent,
    _resnet_model_class,
)
from douzero_advisor.model_service.checkpoint_errors import (
    CheckpointIntegrityError,
    CheckpointStructureError,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models" / "resnet2" / "manifest.json"


def _checkpoint(position: str) -> Path:
    return MANIFEST.parent / f"{position}.ckpt"


def _observation(action_count: int) -> dict[str, np.ndarray]:
    return {
        "z_batch": np.zeros((action_count, 40, 54), dtype=np.float32),
        "x_batch": np.zeros((action_count, 15), dtype=np.float32),
    }


@pytest.mark.parametrize("position", sorted(SUPPORTED_POSITIONS))
def test_real_checkpoint_strictly_loads_and_returns_a_legal_action(position: str) -> None:
    agent = SafeResNet2Agent.from_manifest(position, MANIFEST)
    legal_actions = [[], [3], [4, 4]]

    action = agent.act_observation(_observation(len(legal_actions)), legal_actions)

    assert agent.device == torch.device("cpu")
    assert not agent.model.training
    assert action in legal_actions
    assert agent.checkpoint_sha256 == TRUSTED_RESNET2_SHA256[position]


def test_manifest_pins_upstream_and_all_three_checkpoint_digests() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["upstream_commit"] == PINNED_UPSTREAM_COMMIT
    assert manifest["upstream_repository"] == PINNED_UPSTREAM_REPOSITORY
    assert manifest["license"] == PINNED_LICENSE
    assert manifest["device_default"] == PINNED_DEVICE_DEFAULT
    assert set(manifest["models"]) == SUPPORTED_POSITIONS
    for position, expected_digest in TRUSTED_RESNET2_SHA256.items():
        checkpoint = MANIFEST.parent / manifest["models"][position]["path"]
        assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == expected_digest
        assert checkpoint.stat().st_size == TRUSTED_RESNET2_FILE_SIZE[position]
        assert manifest["models"][position]["file_size"] == checkpoint.stat().st_size
        assert manifest["models"][position]["model_type"] == PINNED_MODEL_TYPE


def test_rejects_checkpoint_byte_replacement(tmp_path: Path) -> None:
    replaced = tmp_path / "landlord.ckpt"
    replaced_bytes = bytearray(_checkpoint("landlord").read_bytes())
    replaced_bytes[-1] ^= 1
    replaced.write_bytes(replaced_bytes)

    with pytest.raises(CheckpointIntegrityError, match="SHA-256 mismatch"):
        SafeResNet2Agent("landlord", replaced)


def test_rejects_checkpoint_file_size_mismatch(tmp_path: Path) -> None:
    truncated = tmp_path / "landlord.ckpt"
    truncated.write_bytes(_checkpoint("landlord").read_bytes()[:-1])

    with pytest.raises(CheckpointIntegrityError, match="file size mismatch"):
        SafeResNet2Agent("landlord", truncated)


@pytest.mark.parametrize("mutation", ["missing", "unexpected", "shape"])
def test_rejects_non_exact_state_dict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    state = torch.load(_checkpoint("landlord"), map_location="cpu", weights_only=True)
    state = OrderedDict(state)
    first_key = next(iter(state))
    if mutation == "missing":
        state.pop(first_key)
        message = "missing="
    elif mutation == "unexpected":
        state["not_a_model_parameter"] = torch.zeros(1)
        message = "unexpected="
    else:
        state[first_key] = state[first_key][0:1]
        message = "shape mismatch"
    malformed = tmp_path / "landlord.ckpt"
    torch.save(state, malformed)
    digest = hashlib.sha256(malformed.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "douzero_advisor.engine.resnet2_agent.TRUSTED_RESNET2_SHA256",
        {**TRUSTED_RESNET2_SHA256, "landlord": digest},
    )
    monkeypatch.setattr(
        "douzero_advisor.engine.resnet2_agent.TRUSTED_RESNET2_FILE_SIZE",
        {**TRUSTED_RESNET2_FILE_SIZE, "landlord": malformed.stat().st_size},
    )

    with pytest.raises(CheckpointStructureError, match=message):
        SafeResNet2Agent("landlord", malformed)


def test_load_uses_weights_only_cpu_and_strict_state_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_torch_load = torch.load
    captured: dict[str, object] = {}

    def recording_load(*args, **kwargs):
        captured.update(kwargs)
        return real_torch_load(*args, **kwargs)

    original_load_state_dict = nn.Module.load_state_dict

    def recording_load_state_dict(self, state_dict, strict=True, assign=False):
        captured["strict"] = strict
        return original_load_state_dict(self, state_dict, strict=strict, assign=assign)

    monkeypatch.setattr(torch, "load", recording_load)
    monkeypatch.setattr(nn.Module, "load_state_dict", recording_load_state_dict)

    SafeResNet2Agent("landlord", _checkpoint("landlord"))

    assert captured["map_location"] == "cpu"
    assert captured["weights_only"] is True
    assert captured["strict"] is True


def test_argmax_maps_to_the_matching_legal_action() -> None:
    agent = SafeResNet2Agent("landlord", _checkpoint("landlord"))

    class FixedValues(nn.Module):
        def forward(self, z, x, return_value=False):
            del z, x, return_value
            return {"values": torch.tensor([[0.1], [3.0], [-1.0]])}

    agent.model = FixedValues()
    legal_actions = [[], [17], [20, 30]]

    assert agent.act_observation(_observation(3), legal_actions) == [17]


def test_rejects_observation_shape_not_matching_legal_actions() -> None:
    agent = SafeResNet2Agent("landlord", _checkpoint("landlord"))

    with pytest.raises(ValueError, match="z_batch shape mismatch"):
        agent.act_observation(_observation(2), [[], [3], [4]])


def test_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["models"]["landlord"]["path"] = "../landlord.ckpt"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ResNet2ManifestError, match="path is unsafe"):
        SafeResNet2Agent.from_manifest("landlord", manifest)


def test_manifest_rejects_unpinned_digest(tmp_path: Path) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["models"]["landlord"]["sha256"] = "0" * 64
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ResNet2ManifestError, match="digest is not trusted"):
        SafeResNet2Agent.from_manifest("landlord", manifest)


def test_manifest_rejects_unpinned_repository(tmp_path: Path) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["upstream_repository"] = "https://example.invalid/replacement"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ResNet2ManifestError, match="repository is not pinned"):
        SafeResNet2Agent.from_manifest("landlord", manifest)


@pytest.mark.parametrize("mutation", ["missing", "unexpected"])
def test_manifest_rejects_non_exact_top_level_fields(
    tmp_path: Path,
    mutation: str,
) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if mutation == "missing":
        raw.pop("license")
        message = "missing=.*license"
    else:
        raw["architecture"] = "resnet"
        message = "unexpected=.*architecture"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ResNet2ManifestError, match=message):
        SafeResNet2Agent.from_manifest("landlord", manifest)


@pytest.mark.parametrize("mutation", ["missing", "unexpected"])
def test_manifest_rejects_non_exact_model_fields(
    tmp_path: Path,
    mutation: str,
) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = raw["models"]["landlord"]
    if mutation == "missing":
        entry.pop("model_type")
        message = "missing=.*model_type"
    else:
        entry["device"] = "cpu"
        message = "unexpected=.*device"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ResNet2ManifestError, match=message):
        SafeResNet2Agent.from_manifest("landlord", manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("license", "MIT", "license is not pinned"),
        ("device_default", "cuda", "device_default must be cpu"),
    ],
)
def test_manifest_rejects_untrusted_top_level_contract(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw[field] = value
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ResNet2ManifestError, match=message):
        SafeResNet2Agent.from_manifest("landlord", manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("file_size", 1, "file size is not trusted"),
        ("model_type", "other", "model type is not trusted"),
    ],
)
def test_manifest_rejects_untrusted_model_metadata(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["models"]["landlord"][field] = value
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ResNet2ManifestError, match=message):
        SafeResNet2Agent.from_manifest("landlord", manifest)


@pytest.mark.parametrize(
    ("input_name", "non_finite"),
    [("z_batch", np.nan), ("x_batch", np.inf)],
)
def test_rejects_non_finite_observation(
    input_name: str,
    non_finite: float,
) -> None:
    agent = SafeResNet2Agent("landlord", _checkpoint("landlord"))
    observation = _observation(2)
    observation[input_name].flat[0] = non_finite

    with pytest.raises(ValueError, match=f"{input_name} contains non-finite"):
        agent.act_observation(observation, [[], [3]])


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf")])
def test_rejects_non_finite_model_output(non_finite: float) -> None:
    agent = SafeResNet2Agent("landlord", _checkpoint("landlord"))

    class NonFiniteValues(nn.Module):
        def forward(self, z, x, return_value=False):
            del z, x, return_value
            return {"values": torch.tensor([[0.1], [non_finite]])}

    agent.model = NonFiniteValues()

    with pytest.raises(RuntimeError, match="output contains non-finite"):
        agent.act_observation(_observation(2), [[], [3]])


def test_vendored_model_has_exact_checkpoint_key_and_shape_contract() -> None:
    model = _resnet_model_class()()
    state = torch.load(_checkpoint("landlord"), map_location="cpu", weights_only=True)

    assert set(model.state_dict()) == set(state)
    assert {
        key: tuple(value.shape) for key, value in model.state_dict().items()
    } == {key: tuple(value.shape) for key, value in state.items()}
