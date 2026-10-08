from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib

import douzero_advisor.recognition_service.model_worker as recognition_entrypoint

ROOT = Path(__file__).parents[1]


# 验证模型服务拥有独立模块入口，查看帮助时不会加载模型或要求 Windows 环境。
def test_model_service_module_help_is_independent_of_runtime_resources() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "douzero_advisor.model_service", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--manifest" in completed.stdout
    assert "--device" in completed.stdout


# 验证模型入口只加载推理合同，不会连带导入截图、旧监听器、UI 或自动输入代码。
def test_model_service_import_does_not_load_recognition_or_legacy_runtime() -> None:
    source = """
import sys
import douzero_advisor.model_service.resnet2_worker

forbidden_prefixes = (
    'douzero_advisor.automation',
    'douzero_advisor.capture',
    'douzero_advisor.live',
    'douzero_advisor.recognition_service',
    'douzero_advisor.ui',
    'douzero_advisor.vision',
)
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden_prefixes)
)
if loaded:
    raise SystemExit('forbidden modules loaded: ' + ','.join(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


# 验证识别入口只负责组装并运行一个 CV Worker，不启动 Java、模型或旧 listener。
def test_recognition_service_entrypoint_runs_injected_worker_once(
    monkeypatch,
) -> None:
    calls: list[Path] = []

    class Worker:
        def run(self) -> int:
            return 17

    def build(manifest: Path) -> Worker:
        calls.append(manifest)
        return Worker()

    monkeypatch.setattr(recognition_entrypoint, "build_recognition_worker", build)

    result = recognition_entrypoint.main(
        ["--calibration-manifest", "config/test-live.json"]
    )

    assert result == 17
    assert calls == [Path("config/test-live.json")]


# 验证安装后的命令行脚本分别指向识别、正式模型和局前模型的独立入口。
def test_console_scripts_expose_independent_python_services() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert configuration["project"]["scripts"] == {
        "douzero-recognition-service": (
            "douzero_advisor.recognition_service.model_worker:main"
        ),
        "douzero-model-service": "douzero_advisor.model_service.resnet2_worker:main",
        "douzero-preplay-model-service": "douzero_advisor.preplay.model_worker:main",
    }


# 验证 Windows 启动脚本只启动各自 Python 服务，不回退到旧 listener 或自动输入入口。
def test_windows_launchers_start_only_their_owned_python_service() -> None:
    recognition = (ROOT / "Run-DouZeroRecognitionService.cmd").read_text(
        encoding="utf-8"
    )
    model = (ROOT / "Run-DouZeroModelService.cmd").read_text(encoding="utf-8")
    combined = recognition + model

    assert "-m douzero_advisor.recognition_service" in recognition
    assert "-m douzero_advisor.model_service" in model
    assert "run_live_advisor.py" not in combined
    assert "douzero_advisor.live.listener" not in combined
    assert "automation" not in combined
    assert "java -jar" not in combined.lower()
    assert os.path.basename(".venv\\Scripts\\python.exe") in combined
    assert "chcp 65001" in combined.lower()
    assert 'set "pythonutf8=1"' in combined.lower()
    assert 'set "pythonioencoding=utf-8"' in combined.lower()


# 验证 WGC 和窗口探针共用独立边界，旧的 Win32/GDI 捕获模块已经从生产源码移除。
def test_capture_boundaries_do_not_reintroduce_legacy_win32_capture_module() -> None:
    assert not (ROOT / "src" / "douzero_advisor" / "capture" / "win32_capture.py").exists()
    errors = (ROOT / "src" / "douzero_advisor" / "capture" / "errors.py").read_text(
        encoding="utf-8"
    )
    window = (ROOT / "src" / "douzero_advisor" / "capture" / "window.py").read_text(
        encoding="utf-8"
    )
    wgc = (ROOT / "src" / "douzero_advisor" / "capture" / "wgc_capture.py").read_text(
        encoding="utf-8"
    )

    assert "class CaptureError" in errors
    assert 'WINDOW_CLASS = "UnityWndClass"' in window
    assert "PyWin32WindowFinder" in window
    assert "capture.win32_capture" not in wgc


# 验证 Java 产物可直接运行；脚本默认打包并开启联调日志，也允许显式覆盖启动参数。
def test_windows_java_launcher_delegates_python_processes_to_runtime() -> None:
    launcher = (ROOT / "Run-DouZeroRuntime.cmd").read_text(encoding="utf-8")
    java_pom = (ROOT / "java-runtime" / "pom.xml").read_text(encoding="utf-8")
    service_profile = (
        ROOT
        / "java-runtime"
        / "src"
        / "main"
        / "resources"
        / "application-python-services.yml"
    ).read_text(encoding="utf-8")
    live_profile = (
        ROOT
        / "java-runtime"
        / "src"
        / "main"
        / "resources"
        / "application-live-runtime.yml"
    ).read_text(encoding="utf-8")
    lowered = launcher.lower()

    assert "<goal>repackage</goal>" in java_pom
    assert "timeout-ms: 10000" in service_profile
    assert "keep-alive: true" in service_profile
    assert "RuntimeKeepAlive" in (
        ROOT
        / "java-runtime"
        / "src"
        / "main"
        / "java"
        / "com"
        / "mkuiwu"
        / "douzero"
        / "runtime"
        / "config"
        / "RuntimeKeepAlive.java"
    ).read_text(encoding="utf-8")
    assert "douzero-runtime-0.1.0-snapshot.jar" in lowered
    assert "mvn.cmd -pl java-runtime package" in lowered
    assert "package -dskiptests" in lowered
    assert 'if /i "%arg%"=="--skip-build"' in lowered
    assert 'if /i "%arg%"=="--with-tests"' in lowered
    assert "chcp 65001" in lowered
    assert "-dfile.encoding=utf-8" in lowered
    assert "prepare_startup_workspace.ps1" in lowered
    assert "douzero_run_directory" in lowered
    assert 'set "advisor_java_args=--douzero.logging.level=debug"' in lowered
    assert "*.cmd text eol=crlf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "--spring.profiles.active=!advisor_profile!" in lowered
    assert "--services-only" in lowered
    assert "on-profile: live-runtime" in live_profile
    assert "orchestration:" in live_profile
    assert "enabled: true" in live_profile
    assert "douzero_advisor.preplay.model_worker" in live_profile
    assert "run-douzerorecognitionservice.cmd" not in lowered
    assert "run-douzeromodelservice.cmd" not in lowered
    assert "douzero_advisor" not in launcher


# 验证 GUI 双击入口先走 UAC 自提权；实际 Runtime 启动时才创建归档目录，避免仅打开界面就留下空目录。
def test_windows_gui_launcher_elevates_and_runtime_prepares_retained_startup_workspace() -> None:
    launcher = (ROOT / "Run-DouZeroAdvisor-GUI.cmd").read_text(encoding="utf-8").lower()
    runtime_launcher = (ROOT / "Run-DouZeroRuntime.cmd").read_text(encoding="utf-8").lower()
    elevation = (ROOT / "scripts" / "elevate_gui_launcher.ps1").read_text(
        encoding="utf-8"
    ).lower()
    preparer = (ROOT / "scripts" / "prepare_startup_workspace.ps1").read_text(
        encoding="utf-8"
    ).lower()

    assert "elevate_gui_launcher.ps1" in launcher
    assert "prepare_startup_workspace.ps1" not in launcher
    assert "douzero_run_directory" not in launcher
    assert 'if "%~1"==""' in launcher
    assert "-verb runas" in elevation
    assert "prepare_startup_workspace.ps1" in runtime_launcher
    assert 'set "douzero_run_directory="' in runtime_launcher
    assert "select-object -skip 2" in preparer
    assert 'where-object { $_.name -like "run-*" }' in preparer


# 验证启动目录清理只删除临时仓库 logs/runs 中超过两批的旧目录，不会触及相邻文件。
def test_startup_workspace_retention_keeps_two_newest_runs_only(tmp_path) -> None:
    runs = tmp_path / "logs" / "runs"
    oldest = runs / "run-20200101-000000.000"
    newer = runs / "run-20200102-000000.000"
    oldest.mkdir(parents=True)
    newer.mkdir()
    protected = tmp_path / "outside-logs.txt"
    protected.write_text("keep", encoding="utf-8")

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "prepare_startup_workspace.ps1"),
            "-RepositoryRoot",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    retained = sorted(path.name for path in runs.iterdir() if path.is_dir())
    assert len(retained) == 2
    assert oldest.name not in retained
    assert newer.name in retained
    assert protected.read_text(encoding="utf-8") == "keep"


# 验证共享 IDEA 配置从仓库根目录启动接线 Profile，避免回退到无服务的 default。
def test_idea_launcher_uses_repository_root_and_python_services_profile() -> None:
    configuration = (ROOT / ".run" / "DouZero Python Services.run.xml").read_text(
        encoding="utf-8"
    )

    assert 'name="DouZero Live Runtime"' in configuration
    assert 'value="com.mkuiwu.douzero.runtime.RuntimeApplication"' in configuration
    assert 'name="ACTIVE_PROFILES" value="live-runtime"' in configuration
    assert 'name="WORKING_DIRECTORY" value="$PROJECT_DIR$"' in configuration
    assert "DOUZERO_PREPLAY_LEGACY_ROOT" in configuration
