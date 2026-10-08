package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

import java.io.IOException;
import java.util.List;

/** 可注入的子进程启动边界，便于测试协议而不依赖真实 Python。 */
@FunctionalInterface
public interface ProcessLauncher {
    /** 按已经拆分的命令参数启动一个不经 shell 展开的子进程。 */
    Process launch(List<String> command) throws IOException;
}
