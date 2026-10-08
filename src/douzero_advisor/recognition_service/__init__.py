"""Python CV 常驻识别服务；只交付稳定语义事实，不维护牌局历史。"""

from douzero_advisor.recognition_service.protocol import (
    ContractError,
    RecognitionCommand,
    TaskType,
    parse_command,
)
from douzero_advisor.recognition_service.worker import RecognitionWorker

__all__ = [
    "ContractError",
    "RecognitionCommand",
    "RecognitionWorker",
    "TaskType",
    "parse_command",
]
