"""Minimal ResNet 2.0 inference model from EdwardPooh/douzero-resnet-2.0.

Source commit: 85afd773abd01c411f543d6ade5b99a4fde327d2
License: GPL-3.0-only.  The complete license and provenance record are kept in
``vendor/DouZero_ResNet2`` at the repository root.
"""

from __future__ import annotations

import torch
from torch import nn
import torch.nn.functional as F


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, in_planes: int, planes: int, stride: int = 1) -> None:
        super().__init__()
        self.conv1 = nn.Conv1d(
            in_planes,
            planes,
            kernel_size=(3,),
            stride=(stride,),
            padding=1,
            bias=False,
        )
        self.bn1 = nn.BatchNorm1d(planes)
        self.conv2 = nn.Conv1d(
            planes,
            planes,
            kernel_size=(3,),
            stride=(1,),
            padding=1,
            bias=False,
        )
        self.bn2 = nn.BatchNorm1d(planes)
        self.shortcut = nn.Sequential()
        if stride != 1 or in_planes != self.expansion * planes:
            self.shortcut = nn.Sequential(
                nn.Conv1d(
                    in_planes,
                    self.expansion * planes,
                    kernel_size=(1,),
                    stride=(stride,),
                    bias=False,
                ),
                nn.BatchNorm1d(self.expansion * planes),
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += self.shortcut(x)
        return F.relu(out)


class ResnetModel(nn.Module):
    """The upstream card-play value network for every landlord position."""

    def __init__(self) -> None:
        super().__init__()
        self.in_planes = 80
        self.conv1 = nn.Conv1d(
            40,
            80,
            kernel_size=(3,),
            stride=(2,),
            padding=1,
            bias=False,
        )
        self.bn1 = nn.BatchNorm1d(80)
        self.layer1 = self._make_layer(BasicBlock, 80, 2, stride=2)
        self.layer2 = self._make_layer(BasicBlock, 160, 2, stride=2)
        self.layer3 = self._make_layer(BasicBlock, 320, 2, stride=2)
        self.linear1 = nn.Linear(320 * BasicBlock.expansion * 4 + 15 * 4, 1024)
        self.linear2 = nn.Linear(1024, 512)
        self.linear3 = nn.Linear(512, 256)
        self.linear4 = nn.Linear(256, 1)

    def _make_layer(
        self,
        block: type[BasicBlock],
        planes: int,
        num_blocks: int,
        stride: int,
    ) -> nn.Sequential:
        strides = [stride] + [1] * (num_blocks - 1)
        layers: list[nn.Module] = []
        for layer_stride in strides:
            layers.append(block(self.in_planes, planes, layer_stride))
            self.in_planes = planes * block.expansion
        return nn.Sequential(*layers)

    def forward(
        self,
        z: torch.Tensor,
        x: torch.Tensor,
        return_value: bool = False,
        flags: object | None = None,
        debug: bool = False,
    ) -> dict[str, torch.Tensor]:
        del debug
        out = F.relu(self.bn1(self.conv1(z)))
        out = self.layer1(out)
        out = self.layer2(out)
        out = self.layer3(out)
        out = out.flatten(1, 2)
        out = torch.cat([x, x, x, x, out], dim=-1)
        out = F.leaky_relu_(self.linear1(out))
        out = F.leaky_relu_(self.linear2(out))
        out = F.leaky_relu_(self.linear3(out))
        out = F.leaky_relu_(self.linear4(out))
        if return_value:
            return {"values": out}
        if flags is not None and getattr(flags, "exp_epsilon", 0) > 0:
            raise ValueError("ResNet 2.0 inference does not support exploration")
        return {"action": torch.argmax(out, dim=0)[0], "max_value": torch.max(out)}
