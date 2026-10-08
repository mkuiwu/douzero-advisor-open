"""生产 CV Worker 的 Windows 组件装配。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from douzero_advisor.recognition_service.worker import RecognitionWorker


def build_recognition_worker(manifest_path: str | Path, **worker_options: Any) -> RecognitionWorker:
    """用现有 WGC/标定 Reader 创建无模型、无点击的生产 Worker。"""

    # 生产装配依赖 OpenCV/WGC 和识别侧 Reader；保持为调用期导入，使协议入口、
    # ``--help`` 和健康探测不会顺带加载模型；仅运行期装配允许将光标停到安全区，不发出点击。
    from douzero_advisor.recognition_service.runtime_assembly import build_live_runtime
    from douzero_advisor.recognition_service.worker import RecognitionWorker
    from douzero_advisor.automation.win32_mouse import Win32ClientMouse
    from douzero_advisor.vision.lifecycle_button_reader import LifecycleButtonReader
    from douzero_advisor.vision.settlement_button_reader import SettlementButtonReader

    capture, observation_reader = build_live_runtime(manifest_path)
    mouse = Win32ClientMouse()
    run_directory = os.environ.get("DOUZERO_RUN_DIRECTORY")
    run_root = Path(run_directory) if run_directory else Path("logs")
    worker_options.setdefault("dump_dir", run_root / "recognition-failures")
    # 每局逐帧录制默认开启；用于回放被 UI 覆盖的瞬态对手动作。
    worker_options.setdefault("deal_recording_dir", run_root / "deal-recordings")
    # 新局和结算等慢边界任务在生产环境下按 0.5 秒间隔采样，降低 CPU 占用；实时任务不受影响。
    worker_options.setdefault("slow_task_min_interval", 0.5)
    # 全局采集最小间隔 100ms：限制两轮 capture/observe 之间的最短时间，避免全速跑满 CPU。
    # 慢任务在全局节流基础上再叠加自己的 0.5 秒节流，实时任务（LOCAL_TURN/TURN_END）受 100ms 限制。
    worker_options.setdefault("min_capture_interval", 0.1)
    return RecognitionWorker(
        capture=capture,
        observation_reader=observation_reader,
        lifecycle_reader=LifecycleButtonReader(),
        settlement_reader=SettlementButtonReader(),
        park_cursor=mouse.park,
        **worker_options,
    )
