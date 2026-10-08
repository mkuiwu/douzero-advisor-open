"""execution.v1 协议解析和序列化测试。"""

from __future__ import annotations

import pytest

from douzero_advisor.execution_service.protocol import (
    CONTRACT_VERSION,
    ContractError,
    ExecutionCommand,
    MessageType,
    TaskType,
    ActionType,
    parse_command,
    ready_message,
    success_message,
    uncertain_message,
    failure_message,
    utc_now_iso,
)


def _play_command(
    request_id: str = "req-001",
    *,
    action: str = "PLAY",
    auto_submit: bool = True,
) -> dict:
    """构造一个合法 CARD_PLAY 命令的基础模板。"""
    payload = {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "SUBMIT",
        "taskType": "CARD_PLAY",
        "requestId": request_id,
        "dealId": "deal-001",
        "generation": 3,
        "deadlineMs": 10000,
        "localSeat": "landlord",
        "authoritativeHand": ["3", "3", "5", "X", "D"],
        "actionType": action,
        "autoSubmit": auto_submit,
    }
    if action == "PLAY":
        payload["recommendedCards"] = ["5"]
    return payload


def _preplay_command(action: str = "call", stage: str = "call") -> dict:
    """构造一个合法的局前正向按钮命令。"""
    return {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "SUBMIT",
        "taskType": "PREPLAY_BUTTON",
        "requestId": "preplay-001",
        "dealId": "deal-001",
        "generation": 3,
        "deadlineMs": 10000,
        "stage": stage,
        "action": action,
    }


class TestParseCommand:
    """验证协议命令解析的严格性。"""

    def test_parse_play_command_auto_submit_true(self) -> None:
        """正常出牌命令 + autoSubmit=true 应正确解析所有字段。"""
        command = parse_command(_play_command())
        assert command.message_type == MessageType.SUBMIT
        assert command.task_type == TaskType.CARD_PLAY
        assert command.request_id == "req-001"
        assert command.deal_id == "deal-001"
        assert command.generation == 3
        assert command.deadline_ms == 10000
        assert command.local_seat == "landlord"
        assert command.authoritative_hand == ("3", "3", "5", "X", "D")
        assert command.recommended_cards == ("5",)
        assert command.action_type == ActionType.PLAY
        assert command.auto_submit is True

    def test_parse_pass_command_auto_submit_false(self) -> None:
        """Pass 命令 + autoSubmit=false 不应要求 recommendedCards。"""
        command = parse_command(_play_command("req-002", action="PASS", auto_submit=False))
        assert command.action_type == ActionType.PASS
        assert command.recommended_cards == ()
        assert command.auto_submit is False

    # 场景：Java 为固定自动不出下发 1.5 秒等待。预期：仅 PASS + autoSubmit 才能携带延迟。
    def test_parse_delayed_auto_pass_command(self) -> None:
        raw = _play_command("req-pass-delay", action="PASS", auto_submit=True)
        raw["clickDelayMs"] = 1500

        command = parse_command(raw)

        assert command.click_delay_ms == 1500

    # 场景：普通出牌不能借用自动不出延迟字段。预期：协议在 Python 边界拒绝该命令。
    def test_reject_click_delay_for_play(self) -> None:
        raw = _play_command()
        raw["clickDelayMs"] = 1500

        with pytest.raises(ContractError, match="只允许用于 PASS"):
            parse_command(raw)

    def test_reject_wrong_contract_version(self) -> None:
        """错误的合同版本必须拒绝。"""
        raw = _play_command()
        raw["contractVersion"] = "execution.v0"
        with pytest.raises(ContractError, match="unsupported contractVersion"):
            parse_command(raw)

    def test_reject_unknown_task(self) -> None:
        """未知任务类型必须拒绝。"""
        raw = _play_command()
        raw["taskType"] = "UNKNOWN_TASK"
        with pytest.raises(ContractError):
            parse_command(raw)

    def test_reject_unknown_action(self) -> None:
        """未知动作类型必须拒绝。"""
        raw = _play_command(action="FOLD")
        with pytest.raises(ContractError):
            parse_command(raw)

    def test_reject_unknown_top_level_field(self) -> None:
        """未知顶层字段必须拒绝，防止协议静默漂移。"""
        raw = _play_command()
        raw["unexpected"] = True
        with pytest.raises(ContractError, match="unknown fields"):
            parse_command(raw)

    def test_reject_blank_request_id(self) -> None:
        """空 requestId 必须拒绝。"""
        raw = _play_command()
        raw["requestId"] = "   "
        with pytest.raises(ContractError, match="requestId must be a non-empty string"):
            parse_command(raw)

    def test_reject_zero_generation(self) -> None:
        """零 generation 必须拒绝。"""
        raw = _play_command()
        raw["generation"] = 0
        with pytest.raises(ContractError, match="generation must be a positive integer"):
            parse_command(raw)

    def test_reject_invalid_card_rank(self) -> None:
        """非标准牌面符号必须拒绝。"""
        raw = _play_command()
        raw["authoritativeHand"] = ["3", "BAD"]
        with pytest.raises(ContractError, match="contains invalid semantic cards"):
            parse_command(raw)

    def test_reject_recommended_not_subset(self) -> None:
        """推荐选牌必须是权威手牌的子集。"""
        raw = _play_command()
        raw["recommendedCards"] = ["9"]
        with pytest.raises(ContractError, match="subset of authoritativeHand"):
            parse_command(raw)

    def test_reject_pass_with_recommended_cards(self) -> None:
        """PASS 动作不应携带 recommendedCards。"""
        raw = _play_command(action="PASS")
        raw["recommendedCards"] = ["5"]
        with pytest.raises(ContractError, match="unknown fields"):
            parse_command(raw)

    def test_reject_auto_submit_not_boolean(self) -> None:
        """autoSubmit 必须是布尔值。"""
        raw = _play_command()
        raw["autoSubmit"] = "yes"
        with pytest.raises(ContractError, match="autoSubmit must be a boolean"):
            parse_command(raw)

    def test_parse_preplay_positive_button_command(self) -> None:
        """局前叫地主正向按钮命令应解析为独立任务。"""
        command = parse_command(_preplay_command())
        assert command.task_type is TaskType.PREPLAY_BUTTON
        assert command.preplay_stage == "call"
        assert command.preplay_action == "call"
        assert command.action_type is None

    @pytest.mark.parametrize(
        "action,stage", [("no_call", "call"), ("no_rob", "rob"), ("no_double", "double")]
    )
    def test_reject_negative_preplay_button_command(self, action: str, stage: str) -> None:
        """局前负向动作必须在协议层拒绝，确保不会进入点击执行器。"""
        with pytest.raises(ContractError, match="禁止自动点击"):
            parse_command(_preplay_command(action, stage))


class TestMessageSerialization:
    """验证响应消息序列化。"""

    def _command(self) -> ExecutionCommand:
        return parse_command(_play_command())

    def test_ready_message(self) -> None:
        """ready 消息应包含合同版本和支持的任务类型。"""
        msg = ready_message()
        assert msg["messageType"] == "READY"
        assert msg["contractVersion"] == CONTRACT_VERSION
        assert "CARD_PLAY" in msg["taskTypes"]

    def test_success_message_includes_auto_submit_echo(self) -> None:
        """成功消息应回显 autoSubmit 并携带 verifiedAt。"""
        msg = success_message(self._command(), verifiedAt="2025-06-20T10:00:00Z")
        assert msg["messageType"] == "RESULT"
        assert msg["taskType"] == "CARD_PLAY"
        assert msg["requestId"] == "req-001"
        assert msg["dealId"] == "deal-001"
        assert msg["generation"] == 3
        assert msg["status"] == "OK"
        assert msg["autoSubmitEcho"] is True
        assert msg["verifiedAt"] == "2025-06-20T10:00:00Z"

    def test_uncertain_message(self) -> None:
        """UNCERTAIN 消息应携带详情供 Java 判断。"""
        cmd = parse_command(_play_command("req-002", auto_submit=False))
        msg = uncertain_message(cmd, "button_clicked_but_no_state_change_observed")
        assert msg["messageType"] == "RESULT"
        assert msg["status"] == "UNCERTAIN"
        assert msg["detail"] == "button_clicked_but_no_state_change_observed"

    def test_failure_message(self) -> None:
        """失败消息应携带错误码和详情。"""
        msg = failure_message(self._command(), "HAND_MISMATCH", "current hand differs")
        assert msg["messageType"] == "RESULT"
        assert msg["status"] == "FAILED"
        assert msg["errorCode"] == "HAND_MISMATCH"
        assert msg["errorMessage"] == "current hand differs"

    def test_utc_now_iso_format(self) -> None:
        """时间戳应为 ISO-8601 UTC 格式。"""
        ts = utc_now_iso()
        assert ts.endswith("+00:00") or ts.endswith("Z")
