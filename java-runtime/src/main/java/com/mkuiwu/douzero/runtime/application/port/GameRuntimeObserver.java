package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.FinishReason;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.application.model.PreplaySnapshot;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;

/** 运行时向只读展示层发布阶段和建议的应用端口；不得实现输入或点击。 */
public interface GameRuntimeObserver {
    /** 当前运行时进入新的权威阶段。 */
    void onPhaseChanged(GamePhase phase, String dealId);

    /** 发布已经完成身份与阶段校验的局前只读建议。 */
    void onPreplayAdvice(PreplaySnapshot snapshot, PreplayResult.Recommendation recommendation);

    /** 发布已经完成身份与阶段校验的正式出牌只读结果。 */
    void onPlayAdvice(GameSnapshot snapshot, AdviceResult result);

    /** 本方回合已经确认，正式模型即将生成建议；展示层可据此清除上一条建议。 */
    default void onPlayTurnStarted(GameSnapshot snapshot) {
    }

    /** 当前牌局已统一收口；随后运行时会重新等待新局。 */
    void onGameFinished(String dealId, FinishReason reason);

    /** 返回一个不产生外部效果的观察器，便于纯业务测试或无界面启动。 */
    static GameRuntimeObserver noop() {
        return new GameRuntimeObserver() {
            @Override
            public void onPhaseChanged(GamePhase phase, String dealId) {
            }

            @Override
            public void onPreplayAdvice(
                    PreplaySnapshot snapshot,
                    PreplayResult.Recommendation recommendation
            ) {
            }

            @Override
            public void onPlayAdvice(GameSnapshot snapshot, AdviceResult result) {
            }

            @Override
            public void onGameFinished(String dealId, FinishReason reason) {
            }
        };
    }
}
