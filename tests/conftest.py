"""公开测试默认不读取未随仓库发布的真实游戏截图。"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--with-private-assets",
        action="store_true",
        help="运行依赖本地真实截图素材包的回归；先运行 install_private_assets.py --check",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--with-private-assets"):
        return
    marker = pytest.mark.skip(reason="需要本地真实截图素材包；使用 --with-private-assets 运行")
    for item in items:
        if "private_assets" in item.keywords:
            item.add_marker(marker)
