package com.mkuiwu.douzero.runtime.application;

/** 为每个新局创建独立 Java 权威会话，便于 Spring 组装与业务测试替换。 */
@FunctionalInterface
public interface GameSessionFactory {
    /**
     * 创建尚未进入正式出牌阶段的会话。
     *
     * @param dealId Java 生成的当前牌局唯一标识
     * @param generation 严格递增的当前牌局代次
     * @return 只含局前上下文的新会话
     */
    GameSession create(String dealId, long generation);
}
