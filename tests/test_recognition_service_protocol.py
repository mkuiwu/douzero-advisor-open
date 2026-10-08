from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from douzero_advisor.recognition_service.protocol import (
    ContractError,
    MessageType,
    TaskType,
    TurnEntryMode,
    parse_command,
    parse_result,
    ready_message,
)

CONTRACT_ROOT = Path(__file__).parents[1] / "contracts" / "recognition" / "v1"


@pytest.mark.parametrize("path", sorted(CONTRACT_ROOT.glob("*.example.json")))
def test_contract_examples_are_flat_and_forbid_cv_implementation_fields(path: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert not {"payload", "result", "frame", "roi", "evidenceHash", "actionHistory"}.intersection(value)
    if value["messageType"] in {"SUBMIT", "CANCEL"}:
        parsed = parse_command(value)
        assert parsed.request_id == value["requestId"]
    else:
        parsed = parse_result(value)
        assert parsed["requestId"] == value["requestId"]


def test_all_thirteen_recognition_examples_are_loaded() -> None:
    assert len(list(CONTRACT_ROOT.glob("*.example.json"))) == 13


def test_local_turn_submit_uses_lower_snake_semantics() -> None:
    value = json.loads((CONTRACT_ROOT / "local-turn.submit.example.json").read_text())
    command = parse_command(value)
    assert command.message_type is MessageType.SUBMIT
    assert command.task_type is TaskType.LOCAL_TURN
    assert command.required_actors == ("landlord", "landlord_down")
    assert command.turn_entry_mode is TurnEntryMode.REQUIRE_EXIT_THEN_REENTER


def test_local_turn_rejects_sensor_change_shortcut_mode() -> None:
    value = json.loads((CONTRACT_ROOT / "local-turn.submit.example.json").read_text())
    value["turnEntryMode"] = "require_change_or_exit_then_new"

    with pytest.raises(ContractError, match="require_change_or_exit_then_new"):
        parse_command(value)


def test_cancel_carries_in_deal_identity_but_no_deadline() -> None:
    command = parse_command(
        {
            "contractVersion": "recognition.v1",
            "messageType": "CANCEL",
            "taskType": "DEAL",
            "requestId": "deal-task",
            "dealId": "deal-1",
            "generation": 2,
        }
    )
    assert command.deadline_ms is None
    assert command.deal_id == "deal-1"


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ContractError, match="unknown fields"):
        parse_command(
            {
                "contractVersion": "recognition.v1",
                "messageType": "SUBMIT",
                "taskType": "NEW_GAME",
                "requestId": "new-1",
                "deadlineMs": 1000,
                "evidenceHash": "forbidden",
            }
        )


def test_ready_handshake_advertises_every_supported_task() -> None:
    ready = ready_message()
    assert ready == {
        "contractVersion": "recognition.v1",
        "messageType": "READY",
        "taskTypes": [
            "NEW_GAME",
            "PREPLAY_PROMPT",
            "DEAL",
            "LOCAL_TURN",
            "TURN_END",
            "SETTLEMENT",
        ],
    }


def test_production_entrypoint_import_is_free_of_business_and_automation_modules() -> None:
    script = """
import sys
import douzero_advisor.recognition_service.model_worker
forbidden = {
    'douzero',
    'douzero_advisor.state.models',
    'douzero_advisor.state.tracker',
    'douzero_advisor.pipeline.controller',
    'douzero_advisor.engine.legal_actions',
    'douzero_advisor.engine.resnet2_observation',
    'douzero_advisor.engine.backend',
    'douzero_advisor._vendor.resnet2.models',
    'douzero_advisor.automation.preplay_buttons',
    'douzero_advisor.automation.win32_mouse',
    'douzero_advisor.automation.click_scheduler',
}
loaded = sorted(forbidden.intersection(sys.modules))
if loaded:
    raise SystemExit('forbidden modules loaded: ' + ','.join(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_production_entrypoint_help_does_not_build_windows_runtime() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "douzero_advisor.recognition_service", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--calibration-manifest" in completed.stdout


# 验证生产识别装配不加载业务模型或点击调度；允许仅用于安全停放光标的 Win32 适配器。
def test_runtime_assembly_import_chain_stays_free_of_business_model_and_click_modules() -> None:
    script = """
import sys
from types import SimpleNamespace
import douzero_advisor.recognition_service.runtime_assembly as live_runtime
import douzero_advisor.automation.win32_mouse as mouse_module

manifest = {"capture": {"width": 1455, "height": 819}}
capture = object()
observation_reader = object()
live_runtime._load_manifest = lambda _path: manifest
live_runtime._build_reader = lambda _manifest, *_paths: observation_reader
live_runtime.PyWin32WindowFinder = lambda: object()
live_runtime.WgcWindowCapture = lambda *_args, **_kwargs: capture
mouse_module.Win32ClientMouse = lambda: SimpleNamespace(park=lambda *_args: None)

from douzero_advisor.recognition_service.runtime import build_recognition_worker
worker = build_recognition_worker("fake-manifest.json")
if worker._capture is not capture or worker._observation_reader is not observation_reader:
    raise SystemExit("production assembly did not use the expected capture/reader")

forbidden_exact = {
    'douzero_advisor.state.models',
    'douzero_advisor.state.tracker',
    'douzero_advisor.engine.legal_actions',
    'douzero_advisor.engine.input_equivalence',
    'douzero_advisor.engine.resnet2_observation',
    'douzero_advisor.engine.backend',
    'douzero_advisor._vendor.resnet2.models',
    'douzero_advisor.vision.tracker_reader',
    'douzero_advisor.pipeline.controller',
    'douzero_advisor.automation.preplay_buttons',
    'douzero_advisor.automation.click_scheduler',
}
loaded = sorted(
    name for name in sys.modules
    if name in forbidden_exact or name == 'douzero' or name.startswith('douzero.')
)
if loaded:
    raise SystemExit('forbidden runtime modules loaded: ' + ','.join(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_lightweight_recognition_types_have_no_business_imports() -> None:
    script = """
import sys
from douzero_advisor.recognition_types import ActionResultState, RecoveryCheckpoint
checkpoint = RecoveryCheckpoint(
    generation=1,
    sequence=1,
    evidence_hashes=('hash-1',),
    my_hand=(3,),
    tracker_counts=(0,) * 15,
)
assert ActionResultState.PLAY.value == 'play'
assert checkpoint.sequence == 1
forbidden = sorted(
    name for name in sys.modules
    if name.startswith('douzero_advisor.state.')
    or name.startswith('douzero_advisor.engine.')
    or name.startswith('douzero_advisor.automation.')
    or name == 'douzero'
    or name.startswith('douzero.')
)
if forbidden:
    raise SystemExit('lightweight types imported business modules: ' + ','.join(forbidden))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
