package com.mkuiwu.douzero.runtime.contract.desktop.v1;

import java.util.List;

/**
 * Java 校验后的正式出牌只读结果事件。
 *
 * @param protocol 固定协议版本 desktop.v1
 * @param type 固定事件类型 PLAY_ADVICE
 * @param sequence 当前 Java 实例内单调递增的业务序号
 * @param emittedAt 模型结果通过校验时的 ISO-8601 时间
 * @param serverInstanceId 当前 Java 进程的随机实例标识
 * @param dealId 当前牌局标识
 * @param phase 当前 Java 权威阶段，正常建议只能是 PLAYING
 * @param localSeat 本方在地主坐标系中的稳定座位
 * @param hand 当前 Java 权威本方手牌符号
 * @param bottomCards 已确认的三张底牌符号
 * @param currentSeat 根据完整历史派生的当前行动座位
 * @param lastMove 当前墩仍有效的最近出牌；新墩开始时为空
 * @param history 从地主首手开始的完整 Java 权威动作历史
 * @param resultKind RECOMMENDATION 表示可信建议，UNAVAILABLE 表示必须等待或恢复
 * @param modelId 处理本次请求的模型标识
 * @param recommendedAction 推荐动作；结果不可用时为 null
 * @param actionValue 推荐动作的模型原始回报价值；结果不可用时为 null
 * @param actionMargin 推荐动作相对第二名的原始价值差；结果不可用时为 null
 * @param actionScores 全部候选动作及模型原始价值；结果不可用时为空
 * @param failure 稳定失败分类；建议可用时为空字符串
 * @param detail 非敏感失败摘要；建议可用时为空字符串
 */
public record DesktopPlayAdviceEvent(
        String protocol,
        DesktopEventType type,
        long sequence,
        String emittedAt,
        String serverInstanceId,
        String dealId,
        String phase,
        String localSeat,
        List<String> hand,
        List<String> bottomCards,
        String currentSeat,
        List<String> lastMove,
        List<DesktopHistoryItem> history,
        String resultKind,
        String modelId,
        DesktopAction recommendedAction,
        Double actionValue,
        Double actionMargin,
        List<DesktopActionScore> actionScores,
        String failure,
        String detail
) implements DesktopEvent {
}
