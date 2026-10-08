"""ResNet2 受信任权重加载的公开异常类型。"""

from __future__ import annotations


class CheckpointIntegrityError(ValueError):
    """权重文件的尺寸或摘要与清单不一致。"""


class CheckpointStructureError(ValueError):
    """权重文件不满足受支持 ResNet2 的结构约束。"""
