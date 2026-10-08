package com.mkuiwu.douzero.runtime.infrastructure;

import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.application.model.FinishReason;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.application.model.PreplaySnapshot;
import com.mkuiwu.douzero.runtime.application.port.GameRuntimeObserver;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** 只记录阶段和只读建议摘要的默认观察器；不包含任何输入或点击能力。 */
public final class LoggingGameRuntimeObserver implements GameRuntimeObserver {
    private static final Logger LOGGER = LoggerFactory.getLogger(LoggingGameRuntimeObserver.class);

    @Override
    public void onPhaseChanged(GamePhase phase, String dealId) {
        LOGGER.info("牌局阶段变化 phase={} dealId={}", phase, dealId);
    }

    @Override
    public void onPreplayAdvice(
            PreplaySnapshot snapshot,
            PreplayResult.Recommendation recommendation
    ) {
        LOGGER.info("局前只读建议 dealId={} stage={} action={} model={} score={} "
                        + "threshold={} metadata={}",
                snapshot.dealId(), snapshot.stage(), recommendation.action(),
                recommendation.modelId(), recommendation.score(), recommendation.threshold(),
                recommendation.metadata());
    }

    @Override
    public void onPlayAdvice(GameSnapshot snapshot, AdviceResult result) {
        if (result instanceof AdviceResult.Recommendation recommendation) {
            LOGGER.info("正式出牌只读建议 dealId={} seat={} action={} model={} "
                            + "actionValue={} actionMargin={} candidateCount={} metadata={}",
                    snapshot.dealId(), snapshot.localSeat(), recommendation.action(),
                    recommendation.modelId(), recommendation.actionValue(),
                    recommendation.actionMargin(), recommendation.actionScores().size(),
                    recommendation.metadata());
            LOGGER.debug("正式出牌只读建议候选明细 dealId={} actionScores={}",
                    snapshot.dealId(), recommendation.actionScores());
        } else if (result instanceof AdviceResult.Unavailable unavailable) {
            LOGGER.warn("正式出牌只读建议不可用 dealId={} seat={} model={} failure={} detail={}",
                    snapshot.dealId(), snapshot.localSeat(), unavailable.modelId(),
                    unavailable.failure(), unavailable.detail());
        }
    }

    @Override
    public void onPlayTurnStarted(GameSnapshot snapshot) {
        LOGGER.info("本方回合已确认，正在生成正式建议 dealId={} seat={} currentSeat={}",
                snapshot.dealId(), snapshot.localSeat(), snapshot.currentSeat());
    }

    @Override
    public void onGameFinished(String dealId, FinishReason reason) {
        LOGGER.info("牌局已收口 dealId={} reason={}", dealId, reason);
    }
}
