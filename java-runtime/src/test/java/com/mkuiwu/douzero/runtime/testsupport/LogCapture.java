package com.mkuiwu.douzero.runtime.testsupport;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.slf4j.LoggerFactory;

/** 测试期间临时捕获指定类的日志，并在结束后恢复原日志级别。 */
public final class LogCapture implements AutoCloseable {
    private final Logger logger;
    private final Level previousLevel;
    private final ListAppender<ILoggingEvent> appender;

    private LogCapture(Logger logger) {
        this.logger = logger;
        this.previousLevel = logger.getLevel();
        this.appender = new ListAppender<>();
        this.appender.start();
        this.logger.setLevel(Level.DEBUG);
        this.logger.addAppender(appender);
    }

    /** 开始捕获目标类的 INFO、WARN 和 DEBUG 日志。 */
    public static LogCapture capture(Class<?> target) {
        return new LogCapture((Logger) LoggerFactory.getLogger(target));
    }

    /** 判断指定级别是否出现包含目标片段的格式化消息。 */
    public boolean contains(Level level, String message) {
        return appender.list.stream().anyMatch(event -> event.getLevel() == level
                && event.getFormattedMessage().contains(message));
    }

    @Override
    public void close() {
        logger.detachAppender(appender);
        logger.setLevel(previousLevel);
        appender.stop();
    }
}
