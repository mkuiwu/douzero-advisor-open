package com.mkuiwu.douzero.runtime.application.port;

/** 为 Java 权威状态机生成不复用的牌局和任务身份。 */
public interface RuntimeIdentitySource {
    /** 生成跨运行期唯一的牌局标识。 */
    String nextDealId();

    /** 生成单次异步任务标识；重试和替换任务必须取得新值。 */
    String nextRequestId();

    /** 生成严格递增的牌局代次，用于拒绝上一局迟到结果。 */
    long nextGeneration();
}
