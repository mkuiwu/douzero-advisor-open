package com.mkuiwu.douzero.runtime;

import org.springframework.boot.WebApplicationType;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.builder.SpringApplicationBuilder;

/**
 * 本地非 Web Java 控制层的 Spring Boot 启动入口。
 *
 * <p>这里不放实时捕获、模型加载或输入执行逻辑；这些能力必须按照仓库规范
 * 放到对应的服务边界和基础设施适配器中。</p>
 */
@SpringBootApplication
public final class RuntimeApplication {
    private RuntimeApplication() {
    }

    public static void main(String[] args) {
        new SpringApplicationBuilder(RuntimeApplication.class)
                .web(WebApplicationType.NONE)
                .run(args);
    }
}
