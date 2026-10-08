"""execution.v1 JSONL Worker 主循环。

从 stdin 读取一行一个 JSON 命令，向 stdout 输出一行一个 JSON 结果。
Worker 是无状态的——牌局历史由 Java 维护，Worker 只处理单次执行请求。

内部仍保留"选牌→验证→（条件）点击→验证"的两阶段逻辑，但对 Java 不暴露。
``autoSubmit=false`` 时选牌验证通过即返回 OK，不点击提交按钮；
``autoSubmit=true`` 时选牌通过后自动点击；点击效果由 Java 的 TURN_END 统一确认。
"""

from __future__ import annotations

import json
import sys
import time
from typing import TextIO

from douzero_advisor.execution_service.protocol import (
    ContractError,
    ExecutionCommand,
    TaskType,
    ActionType,
    failure_message,
    parse_command,
    ready_message,
    success_message,
    uncertain_message,
    utc_now_iso,
)
from douzero_advisor.execution_service.runtime import (
    ExecutionRuntime,
    ExecutionRuntimeError,
)


class ExecutionWorker:
    """execution.v1 协议 Worker；只在 Java 显式授权后执行点击。"""

    def __init__(
        self,
        runtime: ExecutionRuntime,
        *,
        stdin: TextIO = sys.stdin,
        stdout: TextIO = sys.stdout,
        log: TextIO = sys.stderr,
        clock: callable = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._stdin = stdin
        self._stdout = stdout
        self._log = log
        self._clock = clock

    def run(self) -> int:
        """输出 READY 后逐行处理命令；返回进程退出码。"""

        self._write(ready_message())
        try:
            for line in self._stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    command = parse_command(raw)
                except (ContractError, json.JSONDecodeError) as error:
                    self._log.write(f"[execution] 协议错误: {error}\n")
                    self._log.flush()
                    continue
                if command.message_type.value == "CANCEL":
                    self._handle_cancel(command)
                elif command.task_type is TaskType.CARD_PLAY:
                    self._handle_card_play(command)
                elif command.task_type is TaskType.PREPLAY_BUTTON:
                    self._handle_preplay_button(command)
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self._runtime.close()
        return 0

    # ===== CARD_PLAY =====

    def _handle_card_play(self, command: ExecutionCommand) -> None:
        """单次请求单次响应：核对手牌 → 选牌(PASS 跳过) → (条件)提交 → 验证。"""

        deadline = self._clock() + command.deadline_ms / 1000.0

        # 1. 截图核对手牌（PASS 也需要核对，确保状态正确）。
        try:
            frame = self._runtime.capture_frame()
        except ExecutionRuntimeError as error:
            self._write(failure_message(command, "INTERNAL_ERROR", str(error)))
            return

        hand = self._runtime.read_hand(frame)
        if hand is None:
            self._write(
                failure_message(
                    command,
                    "HAND_MISMATCH",
                    "无法读取当前手牌，无法与 Java 权威手牌核对",
                )
            )
            return

        # 手牌一致性硬校验：当前手牌必须与 Java 权威手牌完全一致。
        if tuple(hand.cards) != command.authoritative_hand:
            self._write(
                failure_message(
                    command,
                    "HAND_MISMATCH",
                    f"当前手牌 {list(hand.cards)} 与权威手牌 {list(command.authoritative_hand)} 不一致",
                )
            )
            return

        # 2. PLAY 动作执行选牌；PASS 跳过选牌。
        if command.action_type is ActionType.PLAY:
            from collections import Counter

            if Counter(command.recommended_cards) - Counter(hand.cards):
                self._write(
                    failure_message(
                        command,
                        "RECOMMENDED_NOT_IN_HAND",
                        "建议出的牌不在当前手牌内",
                    )
                )
                return

            if self._clock() > deadline:
                self._write(failure_message(command, "TIMEOUT", "执行任务超过截止时间"))
                return

            selection_result = self._runtime.selection_executor.select(
                hand, command.recommended_cards
            )
            if not selection_result.success:
                error_code = self._map_selection_reason(selection_result.reason)
                self._write(failure_message(command, error_code, selection_result.reason))
                return

        # 3. autoSubmit=false 时，选牌验证通过即返回 OK，不点击提交按钮。
        if not command.auto_submit:
            if command.action_type is ActionType.PLAY:
                self._hover_play_button_after_selection(command)
            else:
                # PASS 仍沿用现有归位逻辑；本变更不触碰“要不起 / 不出”的执行路径。
                self._park_cursor(command, after_click=True)
            self._write(success_message(command, verifiedAt=utc_now_iso()))
            return

        # 4. autoSubmit=true：等待受限延迟后重新读取按钮并点击；TURN_END 负责点击后确认。
        self._submit_and_verify(command, deadline)

    # ===== PREPLAY_BUTTON =====

    def _handle_preplay_button(self, command: ExecutionCommand) -> None:
        """连续读取局前按钮，只点击协议明确授权的正向动作。"""

        from types import SimpleNamespace

        action = command.preplay_action
        stage = command.preplay_stage
        if action is None or stage is None:
            self._write(failure_message(command, "INTERNAL_ERROR", "局前执行命令缺少阶段或动作"))
            return

        # 协议层已经拒绝负向动作；这里再次硬拦截，防止未来调用路径绕过解析器。
        if action not in {"call", "rob", "double", "super_double"}:
            self._write(failure_message(command, "INTERNAL_ERROR", "局前负向动作禁止点击"))
            return

        from douzero_advisor.automation.preplay_buttons import PreplayButtonExecutor

        deadline = self._clock() + command.deadline_ms / 1000.0
        executor = PreplayButtonExecutor(
            click_client=self._runtime.mouse.click,
            timeout_seconds=max(0.1, command.deadline_ms / 1000.0),
            clock=self._clock,
        )
        advice = SimpleNamespace(action=action)
        clicked = False
        last_read = None
        while self._clock() < deadline:
            try:
                frame = self._runtime.capture_frame()
                last_read = self._runtime.read_lifecycle_buttons(frame)
            except ExecutionRuntimeError as error:
                self._write(failure_message(command, "INTERNAL_ERROR", str(error)))
                return
            except Exception as error:
                self._write(failure_message(command, "INTERNAL_ERROR", f"局前按钮识别失败: {error}"))
                return

            outcome = executor.observe(last_read, advice)
            if outcome is not None:
                if outcome.status == "clicked" and outcome.reason == "transition_confirmed":
                    self._write(success_message(command, verifiedAt=utc_now_iso()))
                    return
                if outcome.status == "clicked":
                    clicked = True
                    self._park_cursor(command, after_click=True)
                elif outcome.reason == "post_click_not_confirmed":
                    self._write(uncertain_message(command, outcome.reason))
                    return
                elif outcome.reason == "preplay_stage_changed":
                    if clicked:
                        self._write(success_message(command, verifiedAt=utc_now_iso()))
                    else:
                        self._write(failure_message(command, "BUTTON_NOT_FOUND", outcome.reason))
                    return
                elif outcome.reason.startswith("click_failed"):
                    self._write(failure_message(command, "INTERNAL_ERROR", outcome.reason))
                    return
                elif outcome.reason in {"target_button_timeout", "no_double_wait_timeout"}:
                    self._write(failure_message(command, "BUTTON_NOT_FOUND", outcome.reason))
                    return
            time.sleep(0.05)

        if clicked:
            self._write(uncertain_message(command, "post_click_not_confirmed"))
        else:
            self._write(failure_message(command, "TIMEOUT", "局前按钮执行超过截止时间"))

    def _submit_and_verify(self, command: ExecutionCommand, deadline: float) -> None:
        """等待后重新确认动作按钮并点击；点击结果交给 Java TURN_END 判定。"""

        from douzero_advisor.execution_service.protocol import ActionType

        if not self._wait_for_click_delay(command, deadline):
            return

        try:
            frame = self._runtime.capture_frame()
        except ExecutionRuntimeError as error:
            self._write(failure_message(command, "INTERNAL_ERROR", str(error)))
            return

        # 延迟窗口结束后再次核对手牌，不能根据建议刚到达时的旧画面点击。
        hand = self._runtime.read_hand(frame)
        if hand is None or tuple(hand.cards) != command.authoritative_hand:
            self._write(
                failure_message(
                    command,
                    "HAND_MISMATCH",
                    "自动不出等待后当前手牌已不可读或与 Java 权威手牌不一致",
                )
            )
            return

        buttons = self._runtime.read_action_buttons(frame)
        if not buttons.actionable:
            self._write(
                failure_message(
                    command,
                    "BUTTON_NOT_FOUND",
                    "画面中找不到出牌或不出按钮",
                )
            )
            return

        button_name = "play"
        if command.action_type is ActionType.PASS:
            button_name = "cannot_beat" if "cannot_beat" in buttons.buttons else "pass"
        if button_name not in buttons.buttons:
            self._write(
                failure_message(
                    command,
                    "BUTTON_NOT_FOUND",
                    f"画面中找不到{button_name}按钮",
                )
            )
            return

        target_point = buttons.buttons[button_name].center

        # 调度最终点击：时间窗口 + 证据新鲜度 + 代际授权。
        not_before = self._clock()
        not_after = deadline
        evidence_not_before = not_before - 0.55  # 证据新鲜度阈值 0.55 秒

        def authority_check() -> bool:
            # 简化版：只要 Worker 进程存活即视为授权。
            # 完整实现需要校验 Java generation 仍匹配，由 Java 侧取消机制兜底。
            return True

        schedule = self._runtime.click_scheduler.schedule(
            not_before=not_before,
            not_after=not_after,
            evidence_not_before=evidence_not_before,
            authority_check=authority_check,
        )
        schedule.publish(point=target_point, observed_at=self._clock())

        # 等待点击完成（轮询 outcome，带短睡眠避免忙等）。
        outcome = None
        while self._clock() < not_after:
            outcome = schedule.take_outcome()
            if outcome is not None:
                break
            time.sleep(0.01)

        if outcome is None:
            schedule.cancel()
            self._write(failure_message(command, "TIMEOUT", "提交点击超过截止时间"))
            return

        if outcome.status == "clicked":
            # 物理点击已经发出，效果由 Java 随后的 TURN_END 视觉任务唯一确认。
            self._park_cursor(command, after_click=True)
            self._write(success_message(command, verifiedAt=utc_now_iso()))
        elif outcome.status == "cancelled":
            self._write(
                failure_message(
                    command,
                    "EVIDENCE_STALE",
                    f"点击被取消: {outcome.reason}",
                )
            )
        else:  # uncertain
            self._write(uncertain_message(command, f"点击不确定: {outcome.reason}"))

    def _wait_for_click_delay(self, command: ExecutionCommand, deadline: float) -> bool:
        """等待 PASS 的固定人工接管窗口；期间检测到左键按下即取消。"""

        delay_seconds = command.click_delay_ms / 1000.0
        if delay_seconds <= 0:
            return True
        not_before = self._clock() + delay_seconds
        while self._clock() < not_before:
            if self._runtime.mouse.is_user_left_button_pressed():
                self._write(failure_message(command, "USER_INTERVENTION", "用户在自动不出等待期间按下鼠标"))
                return False
            if self._clock() >= deadline:
                self._write(failure_message(command, "TIMEOUT", "自动不出等待超过执行截止时间"))
                return False
            time.sleep(min(0.05, not_before - self._clock()))
        if self._runtime.mouse.is_user_left_button_pressed():
            self._write(failure_message(command, "USER_INTERVENTION", "用户在自动不出点击前按下鼠标"))
            return False
        return True

    def _park_cursor(self, command: ExecutionCommand, *, after_click: bool = False) -> None:
        """点击或选牌完成后归位鼠标；点击路径先留出 UI 响应时间。"""

        park = getattr(
            self._runtime.mouse,
            "park_after_click" if after_click else "park",
            None,
        )
        if after_click and not callable(park):
            # 兼容测试替身和旧运行时；生产 Win32 鼠标始终提供带 50ms 等待的实现。
            park = getattr(self._runtime.mouse, "park", None)
        if not callable(park):
            return
        try:
            park()
        except Exception as error:  # noqa: BLE001 - 实际点击已发生，后续 TURN_END 仍负责效果确认
            self._log.write(
                "[execution] 鼠标归位失败 "
                f"requestId={command.request_id}: {type(error).__name__}: {error}\n"
            )
            self._log.flush()

    def _hover_play_button_after_selection(self, command: ExecutionCommand) -> None:
        """选牌成功后仅悬停出牌按钮，保留最终点击给用户。"""
        move_after_click = getattr(self._runtime.mouse, "move_after_click", None)
        if not callable(move_after_click):
            return
        try:
            frame = self._runtime.capture_frame()
            buttons = self._runtime.read_action_buttons(frame)
        except Exception as error:  # noqa: BLE001 - 选牌已完成，悬停失败不得阻断 TURN_END
            self._log.write(
                "[execution] 出牌按钮悬停读取失败 "
                f"requestId={command.request_id}: {type(error).__name__}: {error}\n"
            )
            self._log.flush()
            return
        play = buttons.buttons.get("play") if buttons.actionable else None
        if play is None:
            self._log.write(
                "[execution] 出牌按钮悬停跳过 "
                f"requestId={command.request_id}: play_button_not_found\n"
            )
            self._log.flush()
            return
        try:
            move_after_click(play.center)
        except Exception as error:  # noqa: BLE001 - 选牌已完成，悬停失败不得阻断 TURN_END
            self._log.write(
                "[execution] 出牌按钮悬停失败 "
                f"requestId={command.request_id}: {type(error).__name__}: {error}\n"
            )
            self._log.flush()

    @staticmethod
    def _map_selection_reason(reason: str) -> str:
        """把 HandSelectionResult.reason 映射为协议错误码。"""

        mapping = {
            "user_intervention": "USER_INTERVENTION",
            "recommendation_not_in_hand": "RECOMMENDED_NOT_IN_HAND",
            "hand_changed_during_selection": "HAND_MISMATCH",
            "hand_geometry_shifted": "VERIFY_FAILED",
            "selection_reconciliation_unavailable": "SLOT_NOT_FOUND",
            "selection_reconciliation_limit": "VERIFY_FAILED",
            "hand_unreadable_after_click": "VERIFY_FAILED",
            "clicked_card_not_selected": "VERIFY_FAILED",
            "clicked_card_not_deselected": "VERIFY_FAILED",
        }
        return mapping.get(reason, "VERIFY_FAILED")

    # ===== CANCEL =====

    def _handle_cancel(self, command: ExecutionCommand) -> None:
        """取消命令幂等处理；Worker 无正在执行的长任务时无需操作。"""

        self._log.write(
            f"[execution] 收到取消 requestId={command.request_id} "
            f"taskType={command.task_type.value}\n"
        )
        self._log.flush()

    def _write(self, message: dict) -> None:
        self._stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._stdout.flush()
