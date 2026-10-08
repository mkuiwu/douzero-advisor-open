package com.mkuiwu.douzero.runtime.config;

import java.util.concurrent.CountDownLatch;

/**
 * 非 Web 接线 Profile 的生命周期守护。
 *
 * <p>两个 Python worker 各自拥有后台读取线程，但不能把 JVM 生命周期寄托在子进程
 * 或线程实现细节上；这个非 daemon 线程让 IDEA/脚本进程保持运行，关闭 Spring 上下文
 * 时由 {@link #close()} 唤醒并结束。</p>
 */
final class RuntimeKeepAlive implements AutoCloseable {
    private final CountDownLatch stopped = new CountDownLatch(1);
    private final Thread thread;

    RuntimeKeepAlive() {
        thread = new Thread(this::awaitStop, "douzero-runtime-keep-alive");
        thread.setDaemon(false);
        thread.start();
    }

    private void awaitStop() {
        try {
            stopped.await();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    @Override
    public void close() {
        stopped.countDown();
        thread.interrupt();
    }
}
