from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from douzero_advisor.capture.dpi_awareness import DpiAwarenessError, enable_per_monitor_dpi_awareness
from douzero_advisor.capture.window_probe import GameWindowGeometry, GameWindowGeometryService


def geometry_payload(status: GameWindowGeometry) -> dict[str, object]:
    """把 Python 窗口状态转换成 Electron 可消费的稳定 JSON 字段。"""
    return {
        "found": status.found,
        "currentSize": list(status.current_size) if status.current_size is not None else None,
        "targetSize": list(status.target_size),
        "effectiveSize": list(status.effective_size) if status.effective_size is not None else None,
        "dpi": status.dpi,
        "matchMode": status.match_mode,
        "error": status.error,
        "actionableError": status.actionable_error,
        "matches": status.matches,
    }


def execute(
    action: str,
    service: GameWindowGeometryService,
) -> GameWindowGeometry:
    """仅接受受控检查和显式调整动作，不接受任意 Python 方法名。"""
    if action == "inspect":
        return service.inspect()
    if action == "adjust":
        return service.adjust()
    raise ValueError(f"unsupported geometry action: {action}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DouZero 桌面窗口尺寸桥接")
    parser.add_argument("action", choices=("inspect", "adjust"))
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: GameWindowGeometryService | None = None,
) -> int:
    """执行一次有界窗口操作并向 stdout 输出单行 JSON。"""
    args = _parser().parse_args(argv)
    try:
        enable_per_monitor_dpi_awareness()
    except DpiAwarenessError:
        # 旧进程上下文无法切换时仍返回原始尺寸；GameWindowGeometry 会结合窗口 DPI
        # 识别可换算的虚拟尺寸，未知情况保持不匹配而不是强制改窗口。
        pass
    status = execute(args.action, service or GameWindowGeometryService())
    print(json.dumps(geometry_payload(status), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
