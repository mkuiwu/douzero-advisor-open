package com.mkuiwu.douzero.runtime.contract.common;

/** 跨进程结果的有限状态集合。 */
public enum ContractStatus {
    /** 请求已成功处理，结果可以继续接受业务校验。 */
    OK,

    /** 当前证据或依赖暂不满足条件，调用方必须等待而不能执行动作。 */
    WAIT,

    /** 请求处理失败，需要记录错误并按错误码决定恢复方式。 */
    ERROR
}
