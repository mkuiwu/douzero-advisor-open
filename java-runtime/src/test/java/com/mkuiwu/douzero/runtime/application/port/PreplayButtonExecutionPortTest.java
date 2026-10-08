package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** 局前按钮执行请求的不变量测试。 */
class PreplayButtonExecutionPortTest {
    private static final GameTaskIdentity IDENTITY =
            new GameTaskIdentity("preplay-001", "deal-001", 3);

    @Test
    void acceptsPositiveCallAction() {
        // 场景：叫地主动作与 CALL_LANDLORD 阶段匹配。预期：请求构造成功。
        assertDoesNotThrow(() -> new PreplayButtonExecutionPort.ExecutionRequest(
                IDENTITY, PreplayStage.CALL_LANDLORD, PreplayAction.CALL, 1000));
    }

    @Test
    void rejectsNegativeDoubleAction() {
        // 场景：不加倍动作进入执行端口。预期：构造失败，防止任何点击路径接收它。
        assertThrows(IllegalArgumentException.class,
                () -> new PreplayButtonExecutionPort.ExecutionRequest(
                        IDENTITY, PreplayStage.DOUBLE, PreplayAction.NO_DOUBLE, 1000));
    }
}
