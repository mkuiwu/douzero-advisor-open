package com.mkuiwu.douzero.runtime.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

/**
 * 从 application.yml 读取的类型化运行配置。
 *
 * @param recognition Python CV 持久识别 worker 配置
 * @param model Python 模型服务调用配置
 * @param preplayModel Python 局前模型持久 worker 配置
 * @param execution Python 本方出牌和局前按钮执行 worker 配置；默认关闭，仅授权后启用
 * @param orchestration Java 状态机各完整业务任务的硬截止时间
 * @param desktop Electron 桌面端本机 WebSocket 事件网关配置
 * @param tts 语音播报服务配置
 * @param logging 本地运行日志配置
 * @param statistics 对局统计持久化配置
 */
@Validated
@ConfigurationProperties(prefix = "douzero")
public record RuntimeProperties(
        Recognition recognition,
        Model model,
        PreplayModel preplayModel,
        Execution execution,
        Orchestration orchestration,
        Desktop desktop,
        Tts tts,
        Logging logging,
        Statistics statistics
) {
    /**
     * @param enabled 是否启用真实 Python CV 识别链；默认关闭时不会创建进程或假结果
     * @param command 不经 shell 展开的 Python CV worker 命令和参数
     * @param calibrationManifest Python CV 读取的校准清单路径
     * @param startupTimeoutMs 等待 worker READY 能力声明的最长时间，单位为毫秒
     */
    public record Recognition(boolean enabled, java.util.List<String> command,
                              String calibrationManifest, long startupTimeoutMs) {
        public Recognition {
            command = java.util.List.copyOf(java.util.Objects.requireNonNull(
                    command, "识别 worker 命令不能为空"));
            if (startupTimeoutMs <= 0) {
                throw new IllegalArgumentException("识别 worker 启动超时必须为正数");
            }
            if (enabled && (command.isEmpty() || command.stream().anyMatch(
                    value -> value == null || value.isBlank()))) {
                throw new IllegalArgumentException("启用识别 worker 时必须配置完整命令");
            }
            if (enabled) {
                requireText(calibrationManifest, "启用识别 worker 时必须配置校准清单");
            }
        }
    }

    /**
     * @param enabled 是否启用真实 Python 正式出牌模型；默认关闭时不会创建进程或建议
     * @param command 不经 shell 展开的持久 ResNet2 worker 命令和参数
     * @param name 请求、READY 和响应中必须一致的模型标识，当前只支持 resnet2
     * @param timeoutMs worker READY 与单次推理的硬超时，单位为毫秒
     */
    public record Model(boolean enabled, java.util.List<String> command,
                        String name, long timeoutMs) {
        public Model {
            command = command == null ? java.util.List.of() : java.util.List.copyOf(command);
            if (timeoutMs <= 0) {
                throw new IllegalArgumentException("正式出牌模型超时必须为正数");
            }
            if (enabled) {
                if (command.isEmpty() || command.stream().anyMatch(
                        value -> value == null || value.isBlank())) {
                    throw new IllegalArgumentException("启用正式出牌模型时必须配置完整命令");
                }
                requireText(name, "启用正式出牌模型时必须配置模型标识");
            }
        }
    }

    /**
     * @param enabled 是否启用真实 Python 局前模型；默认关闭时不会创建进程或建议
     * @param command 不经 shell 展开的持久局前模型 worker 命令和参数
     * @param legacyRoot legacy FullAuto 模型代码和制品根目录
     * @param modelId 请求和响应中必须一致的局前模型标识
     * @param timeoutMs 单次局前推理硬超时，单位为毫秒
     */
    public record PreplayModel(boolean enabled, java.util.List<String> command,
                               String legacyRoot, String modelId, long timeoutMs) {
        public PreplayModel {
            command = java.util.List.copyOf(java.util.Objects.requireNonNull(
                    command, "局前模型命令不能为空"));
            if (timeoutMs <= 0) {
                throw new IllegalArgumentException("局前模型超时必须为正数");
            }
            if (enabled) {
                if (command.isEmpty() || command.stream().anyMatch(
                        value -> value == null || value.isBlank())) {
                    throw new IllegalArgumentException("启用局前模型时必须配置完整命令");
                }
                requireText(legacyRoot, "启用局前模型时必须配置 legacy 模型根目录");
                requireText(modelId, "启用局前模型时必须配置模型标识");
            }
        }
    }

    /**
     * @param enabled 是否启用真实 Python 本方出牌执行链；默认关闭，仅用户显式授权后启用
     * @param command 不经 shell 展开的 Python execution worker 命令和参数
     * @param calibrationManifest Python execution worker 读取的校准清单路径
     * @param startupTimeoutMs 等待 worker READY 能力声明的最长时间，单位为毫秒
     * @param executeMs 单次出牌执行任务的 Java 侧硬截止时间，单位为毫秒
     * @param autoSubmit 是否自动点击出牌/不出提交按钮；false 时只选牌不点击，供用户手工确认
     * @param cardPlayEnabled 是否启用正式出牌选牌执行；与局前按钮执行独立
     * @param autoPassEnabled 是否对正式模型明确的 PASS 自动点击“要不起”或“不要”；不影响普通出牌提交
     * @param autoPassDelayMs PASS 建议发布后到最终点击前的固定等待时间，单位毫秒；只用于 PASS
     * @param preplayButtonsEnabled 是否启用叫地主、抢地主和正向加倍按钮点击
     */
    public record Execution(
            boolean enabled,
            java.util.List<String> command,
            String calibrationManifest,
            long startupTimeoutMs,
            long executeMs,
            boolean autoSubmit,
            boolean cardPlayEnabled,
            boolean autoPassEnabled,
            long autoPassDelayMs,
            boolean preplayButtonsEnabled
    ) {
        public Execution {
            command = command == null ? java.util.List.of() : java.util.List.copyOf(command);
            if (startupTimeoutMs <= 0) {
                throw new IllegalArgumentException("执行 worker 启动超时必须为正数");
            }
            if (executeMs <= 0) {
                throw new IllegalArgumentException("执行截止时间必须为正数");
            }
            if (autoPassDelayMs < 0) {
                throw new IllegalArgumentException("自动不出等待时间不能为负数");
            }
            if (enabled) {
                if (command.isEmpty() || command.stream().anyMatch(
                        value -> value == null || value.isBlank())) {
                    throw new IllegalArgumentException("启用执行 worker 时必须配置完整命令");
                }
                requireText(calibrationManifest, "启用执行 worker 时必须配置校准清单");
            }
        }
    }

    /**
     * @param enabled 是否启动 Java 状态机；关闭时仍可只启动独立 Python 服务
     * @param newGameMs 等待新局边界的最长时间，单位为毫秒
     * @param preplayRecognitionMs 单次局前提示识别的最长时间，单位为毫秒
     * @param dealMs 正式牌局初始化事实闭合的最长时间，单位为毫秒
     * @param settlementMs 单个结算旁路 watcher 的最长存活时间，单位为毫秒
     * @param localTurnMs 等待下一次本方稳定回合快照的最长时间，单位为毫秒；由 Python CV 解释并返回最终成功或失败
     * @param turnEndMs 建议后等待当前本方回合结束的最长时间，单位为毫秒；由 Python CV 依据按钮和基线变化确认
     * @param preplayDecisionMs 单次局前模型调用的最长时间，单位为毫秒
     * @param playDecisionMs 单次正式出牌模型调用的最长时间，单位为毫秒
     */
    public record Orchestration(
            boolean enabled,
            long newGameMs,
            long preplayRecognitionMs,
            long dealMs,
            long settlementMs,
            long localTurnMs,
            long turnEndMs,
            long preplayDecisionMs,
            long playDecisionMs
    ) {
        public Orchestration {
            if (newGameMs <= 0 || preplayRecognitionMs <= 0 || dealMs <= 0
                    || settlementMs <= 0 || localTurnMs <= 0 || turnEndMs <= 0
                    || preplayDecisionMs <= 0 || playDecisionMs <= 0) {
                throw new IllegalArgumentException("所有 Java 业务截止时间都必须为正数");
            }
        }
    }

    /**
     * @param enabled 是否启用 Java 到 Electron 的只读事件网关；关闭时不监听任何端口
     * @param port 仅绑定 127.0.0.1 的 WebSocket 端口，取值范围为 1 到 65535
     * @param token Electron 每次联合启动时注入的随机令牌，启用时至少三十二个字符且不得写入日志
     * @param startupTimeoutMs 等待本机 WebSocket 端口开始监听的最长时间，单位为毫秒
     */
    public record Desktop(boolean enabled, int port, String token, long startupTimeoutMs) {
        public Desktop {
            if (port <= 0 || port > 65535) {
                throw new IllegalArgumentException("桌面 WebSocket 端口必须在 1 到 65535 之间");
            }
            if (startupTimeoutMs <= 0) {
                throw new IllegalArgumentException("桌面 WebSocket 启动超时必须为正数");
            }
            if (enabled && (token == null || token.length() < 32)) {
                throw new IllegalArgumentException("启用桌面 WebSocket 时必须注入至少三十二字符的启动令牌");
            }
        }
    }

    /**
     * @param enabled 是否启用语音播报；不影响只读建议主链
     * @param endpoint TTS 服务的 HTTP 地址
     * @param voice TTS 服务使用的音色标识
     */
    public record Tts(boolean enabled, String endpoint, String voice) {
    }

    /**
     * @param directory 日志根目录，支持相对当前工作目录的路径
     * @param fileName 常规运行日志文件名
     * @param errorFileName ERROR 级别独立日志文件名
     * @param warnFileName WARN 级别独立日志文件名
     * @param startupFilePrefix 启动阶段日志文件名前缀
     * @param level DouZero 业务包的日志级别
     * @param rootLevel 根日志级别
     * @param maxHistory 滚动日志最多保留天数
     * @param maxFileSize 单个滚动日志文件上限，使用 Logback 容量格式
     */
    public record Logging(String directory, String fileName, String errorFileName,
                          String warnFileName, String startupFilePrefix,
                          String level, String rootLevel, int maxHistory,
                          String maxFileSize) {
    }

    /**
     * @param enabled 是否启用对局统计持久化
     * @param storage 统计存储实现标识；启用前必须存在对应 Repository 适配器
     */
    public record Statistics(boolean enabled, String storage) {
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
