package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

import java.io.IOException;
import java.util.List;

/** 使用 JDK ProcessBuilder 启动持久 Python worker 的生产实现。 */
public final class DefaultProcessLauncher implements ProcessLauncher {
    @Override
    public Process launch(List<String> command) throws IOException {
        return new ProcessBuilder(List.copyOf(command)).start();
    }
}
