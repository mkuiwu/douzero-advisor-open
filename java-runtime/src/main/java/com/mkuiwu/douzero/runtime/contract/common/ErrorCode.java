package com.mkuiwu.douzero.runtime.contract.common;

/** Java 与 Python 之间可以稳定判断的错误码。 */
public enum ErrorCode {
    /** 没有错误；只允许与成功状态同时使用。 */
    NONE,

    /** 响应所属牌局与当前锁定牌局不一致，必须丢弃。 */
    DEAL_MISMATCH,

    /** 牌局快照缺字段、值冲突或未通过完整性校验。 */
    INVALID_SNAPSHOT,

    /** 指定模型当前不可调用，例如服务未启动或健康检查失败。 */
    MODEL_UNAVAILABLE,

    /** 请求的模型标识未注册，无法选择对应模型策略。 */
    UNSUPPORTED_MODEL,

    /** 模型调用超过请求规定的毫秒级期限，迟到结果必须丢弃。 */
    MODEL_TIMEOUT,

    /** 模型返回的动作不在 Python 固定规则实现生成的合法动作集合内。 */
    ILLEGAL_ACTION,

    /** 请求或响应使用了当前服务不支持的契约版本。 */
    UNSUPPORTED_CONTRACT,

    /** 模型正常完成计算，但当前状态下没有可提供的建议。 */
    NO_RECOMMENDATION
}
