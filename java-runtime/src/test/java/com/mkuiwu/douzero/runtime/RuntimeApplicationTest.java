package com.mkuiwu.douzero.runtime;

import com.mkuiwu.douzero.runtime.config.RuntimeProperties;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import static org.junit.jupiter.api.Assertions.assertEquals;

@SpringBootTest
class RuntimeApplicationTest {
    @Autowired
    private RuntimeProperties properties;

    /** 验证 Spring Boot 能加载应用上下文并完成类型化运行配置绑定。 */
    @Test
    void springBootLoadsTypedConfiguration() {
        assertEquals("resnet2", properties.model().name());
        assertEquals("./logs", properties.logging().directory());
        assertEquals("error.log", properties.logging().errorFileName());
    }
}
