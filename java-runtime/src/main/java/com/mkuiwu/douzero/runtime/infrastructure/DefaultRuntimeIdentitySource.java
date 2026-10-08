package com.mkuiwu.douzero.runtime.infrastructure;

import com.mkuiwu.douzero.runtime.application.port.RuntimeIdentitySource;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;

/** 使用 UUID 和进程内单调计数为 Java 状态机生成不可复用身份。 */
public final class DefaultRuntimeIdentitySource implements RuntimeIdentitySource {
    /** 当前进程已经签发的最大牌局代次。 */
    private final AtomicLong generation = new AtomicLong();

    @Override
    public String nextDealId() {
        return "deal-" + UUID.randomUUID();
    }

    @Override
    public String nextRequestId() {
        return "request-" + UUID.randomUUID();
    }

    @Override
    public long nextGeneration() {
        return generation.incrementAndGet();
    }
}
