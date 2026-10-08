from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


# 验证既有桌面快捷方式目标无需变化，默认 GUI 启动器委托给受控 Electron 联合入口。
def test_default_gui_launcher_starts_electron_desktop() -> None:
    launcher = (ROOT / "Run-DouZeroAdvisor-GUI.cmd").read_text(encoding="utf-8")

    assert "scripts\\launch_electron_desktop.ps1" in launcher
    assert "powershell.exe" in launcher
    assert "scripts\\run_desktop_advisor.py" not in launcher


# 验证桌面启动器只构建并打开 Electron，不会在界面出现前启动 Java 或 Python 服务。
def test_gui_launcher_starts_electron_without_runtime_services() -> None:
    launcher = (ROOT / "scripts" / "launch_electron_desktop.ps1").read_text(encoding="utf-8")

    assert "npm.cmd run build" in launcher
    assert "npm.cmd run start" in launcher
    assert '$env:DOUZERO_REPO_ROOT = $repositoryRoot' in launcher
    assert "Run-DouZeroRuntime.cmd" not in launcher
    assert "DOUZERO_DESKTOP_WS_TOKEN" not in launcher
    assert "taskkill.exe" not in launcher


# 验证只有 Electron 主进程可以创建 Java 与 Python 服务树，renderer 不会接触令牌或进程参数。
def test_main_process_owns_runtime_service_startup() -> None:
    supervisor = (ROOT / "desktop-ui" / "src" / "main" / "runtime-supervisor.ts").read_text(
        encoding="utf-8"
    )
    main_process = (ROOT / "desktop-ui" / "src" / "main" / "index.ts").read_text(encoding="utf-8")

    assert 'resolve(this.repositoryRoot, "Run-DouZeroRuntime.cmd")' in supervisor
    assert "randomBytes(32)" in supervisor
    assert 'DOUZERO_DESKTOP_WS_ENABLED: "true"' in supervisor
    assert 'DOUZERO_DESKTOP_WS_TOKEN: token' in supervisor
    assert '"/d", "/c", "call", launcher' in supervisor
    assert "taskkill.exe" in supervisor
    assert "RuntimeSupervisor" in main_process
    assert "START_RUNTIME" in main_process


# 验证页面载入不触发窗口操作；用户检测到尺寸不符时必须走受控 IPC 自动调整，且未精确命中不能启动服务。
def test_game_window_detection_auto_adjusts_and_requires_exact_match() -> None:
    renderer = (ROOT / "desktop-ui" / "src" / "renderer" / "src" / "App.vue").read_text(
        encoding="utf-8"
    )
    main_process = (ROOT / "desktop-ui" / "src" / "main" / "index.ts").read_text(encoding="utf-8")
    ipc = (ROOT / "desktop-ui" / "src" / "shared" / "ipc.ts").read_text(encoding="utf-8")

    assert "setInterval" not in renderer
    assert "void inspectGeometry()" not in renderer
    assert "检测并自动调整客户端" in renderer
    assert "adjustGameWindow()" in renderer
    assert "geometry.value.matches" in renderer
    assert "尺寸已精确命中，可以启动服务" in renderer
    assert "ADJUST_GAME_WINDOW" in ipc
    assert 'queryGameWindow(root, "adjust")' in main_process


# 验证左侧导航支持保留页面状态的展开/收起切换，收起后由 Element 菜单只展示图标。
def test_renderer_sidebar_can_be_collapsed() -> None:
    renderer = (ROOT / "desktop-ui" / "src" / "renderer" / "src" / "App.vue").read_text(
        encoding="utf-8"
    )
    styles = (ROOT / "desktop-ui" / "src" / "renderer" / "src" / "styles.css").read_text(
        encoding="utf-8"
    )

    assert "sidebarCollapsed" in renderer
    assert ":collapse=\"sidebarCollapsed\"" in renderer
    assert "sidebar-toggle" in renderer
    assert "is-collapsed" in styles
    assert "grid-template-columns: 76px 1fr" in styles


# 验证局前建议只作为事件记录，不在正式出牌建议卡片中长期占位。
def test_renderer_does_not_pin_the_last_preplay_advice() -> None:
    renderer = (ROOT / "desktop-ui" / "src" / "renderer" / "src" / "App.vue").read_text(
        encoding="utf-8"
    )
    assert "preplay-strip" not in renderer
    assert "runtime.preplay" not in renderer


# 验证旧 Tkinter 回退入口不再随产品根目录发布，避免操作员误入废弃启动链。
def test_legacy_tkinter_launcher_is_not_shipped() -> None:
    assert not (ROOT / "Run-DouZeroAdvisor-Tkinter.cmd").exists()
