package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.AdviceQuery;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;

/** Java Core 面向 Python DouZero 的语义决策端口。 */
public interface DecisionPort {
    /**
     * 根据已确认牌局状态请求一次只读建议。
     *
     * @param query 不包含模型专属编码的语义决策请求
     * @return 已校验建议或明确失败原因
     */
    AdviceResult decide(AdviceQuery query);
}
