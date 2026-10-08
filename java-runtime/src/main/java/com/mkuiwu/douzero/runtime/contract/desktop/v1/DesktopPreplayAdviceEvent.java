package com.mkuiwu.douzero.runtime.contract.desktop.v1;

import java.util.List;

/**
 * Java 校验后的局前只读建议事件。
 *
 * @param protocol 固定协议版本 desktop.v1
 * @param type 固定事件类型 PREPLAY_ADVICE
 * @param sequence 当前 Java 实例内单调递增的业务序号
 * @param emittedAt 建议通过校验时的 ISO-8601 时间
 * @param serverInstanceId 当前 Java 进程的随机实例标识
 * @param dealId 当前牌局标识
 * @param generation 当前牌局代次，用于说明建议所属生命周期
 * @param stage 当前实际观察到的局前提示阶段
 * @param hand 当前稳定确认的本方牌面符号
 * @param bottomCards 当前已确认的底牌符号；尚未展示时为空
 * @param availableActions 当前画面真实提供的语义动作
 * @param callPromptSeen 本局是否观察过本方叫地主提示
 * @param robPromptSeen 本局是否观察过本方抢地主提示
 * @param modelId 产生建议的局前模型标识
 * @param action 模型推荐的局前语义动作，不代表用户已经执行
 * @param score 模型原始评分，不是概率或胜率
 * @param threshold 本次建议使用的业务阈值
 */
public record DesktopPreplayAdviceEvent(
        String protocol,
        DesktopEventType type,
        long sequence,
        String emittedAt,
        String serverInstanceId,
        String dealId,
        long generation,
        String stage,
        List<String> hand,
        List<String> bottomCards,
        List<String> availableActions,
        boolean callPromptSeen,
        boolean robPromptSeen,
        String modelId,
        String action,
        double score,
        double threshold
) implements DesktopEvent {
}
