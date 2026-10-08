package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.PreplayQuery;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;

/** Java Core 面向 Python 局前模型的同步语义决策端口。 */
public interface PreplayDecisionPort {
    /**
     * 根据已确认局前提示请求一次只读建议；调用方必须在独立决策线程执行。
     *
     * @param query 不包含按钮坐标、模型编码或点击语义的局前请求
     * @return 已校验建议或明确失败原因
     */
    PreplayResult decide(PreplayQuery query);
}
