package com.mkuiwu.douzero.runtime.application.model;

import java.util.Objects;

/**
 * Python CV 完成内部重试和恢复后仍无法交付业务结果时的明确失败。
 *
 * @param code Java 可以稳定处理的失败分类
 * @param detail 面向诊断的简短原因；不得包含截图内容或视觉内部状态转储
 */
public record RecognitionFailure(
        RecognitionFailureCode code,
        String detail
) {
    public RecognitionFailure {
        Objects.requireNonNull(code, "识别失败分类不能为空");
        if (detail == null || detail.isBlank()) {
            throw new IllegalArgumentException("识别失败说明不能为空");
        }
    }
}
