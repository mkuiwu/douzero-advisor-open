"""本地素材安装必须先校验全部文件，再写入公开仓库工作树。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import install_private_assets


def _entry(path: str, data: bytes) -> tuple[Path, str]:
    return Path(path), hashlib.sha256(data).hexdigest()


def test_install_rejects_tampered_pack_before_copying_any_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """素材包中任意图片的哈希不符时，不得安装前面已经校验通过的图片。"""
    repo = tmp_path / "repo"
    source = tmp_path / "source"
    repo.mkdir()
    source.mkdir()
    entries = [
        _entry("config/live-calibration/first.jpg", b"first"),
        _entry("tests/fixtures/core_cases/second.jpg", b"second"),
    ]
    for relative, data in ((entries[0][0], b"first"), (entries[1][0], b"tampered")):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setattr(install_private_assets, "REPOSITORY_ROOT", repo)

    with pytest.raises(ValueError, match="SHA-256 不匹配"):
        install_private_assets.install(source, entries)

    assert not (repo / entries[0][0]).exists()


def test_manifest_rejects_path_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """恶意清单不能把本地素材写到公开仓库之外。"""
    manifest_path = tmp_path / "private-assets.sha256.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "files": [{"path": "../outside.jpg", "sha256": "a" * 64}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(install_private_assets, "MANIFEST_PATH", manifest_path)

    with pytest.raises(ValueError, match="条目无效"):
        install_private_assets._entries()
