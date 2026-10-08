package com.mkuiwu.douzero.runtime.architecture;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertFalse;

class SemanticBoundaryTest {
    private static final Path MAIN_JAVA = Path.of("src/main/java/com/mkuiwu/douzero/runtime");

    /** 验证领域层和应用层不会依赖外部编码、协议 DTO 或视觉/框架类库。 */
    @Test
    void domainAndApplicationDoNotDependOnExternalEncodingsOrLibraries() throws IOException {
        List<String> forbidden = List.of(
                "List<Integer>",
                "environmentCode",
                "com.fasterxml.jackson",
                "java.nio.ByteBuffer",
                "byte[]",
                "contract.model",
                "infrastructure."
        );

        for (String layer : List.of("domain", "application")) {
            try (var files = Files.walk(MAIN_JAVA.resolve(layer))) {
                for (Path file : files.filter(path -> path.toString().endsWith(".java")).toList()) {
                    String source = Files.readString(file);
                    for (String token : forbidden) {
                        assertFalse(source.contains(token),
                                () -> file + " 不得依赖外部边界标记: " + token);
                    }
                }
            }
        }
    }

    /** 验证应用层保持按能力拆分的识别端口，不重新引入万能识别接口。 */
    @Test
    void applicationDoesNotReintroduceUniversalRecognitionContract() throws IOException {
        List<String> forbiddenTypeNames = List.of(
                "RecognitionPort.java",
                "RecognitionIntent.java",
                "RecognitionFact.java",
                "RecognitionTask.java",
                "GameAction.java"
        );

        try (var files = Files.walk(MAIN_JAVA.resolve("application"))) {
            List<String> actualNames = files
                    .filter(Files::isRegularFile)
                    .map(path -> path.getFileName().toString())
                    .toList();
            for (String forbiddenTypeName : forbiddenTypeNames) {
                assertFalse(actualNames.contains(forbiddenTypeName),
                        () -> "不得恢复万能识别或动作容器: " + forbiddenTypeName);
            }
        }
    }
}
