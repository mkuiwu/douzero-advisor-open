package com.mkuiwu.douzero.runtime.application.model;

/** 模型端口无法提供可信建议时，控制层可以稳定处理的失败原因。 */
public enum AdviceFailure {
    /** 外部模型服务当前不可用。 */
    MODEL_UNAVAILABLE,

    /** 外部模型在截止时间内没有完成。 */
    MODEL_TIMEOUT,

    /** 配置的模型标识不受支持。 */
    UNSUPPORTED_MODEL,

    /** Java 与模型服务使用的跨进程协议版本不兼容。 */
    UNSUPPORTED_CONTRACT,

    /** 响应身份、结构或牌面内容无效。 */
    INVALID_RESPONSE,

    /** 模型服务返回动作不在 Python 生成的合法动作集合内。 */
    ILLEGAL_ACTION,

    /** 模型明确表示当前没有可提供的建议。 */
    NO_RECOMMENDATION
}
