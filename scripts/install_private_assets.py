"""从用户本地素材包安装并校验公开仓库不附带的真实截图。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / "config" / "private-assets.sha256.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entries() -> list[tuple[Path, str]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("私有素材清单格式无效")
    entries: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ValueError("私有素材清单条目格式无效")
        path = Path(entry.get("path", ""))
        digest = entry.get("sha256")
        if (
            path.is_absolute()
            or not path.parts
            or ".." in path.parts
            or path in seen
            or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError(f"私有素材清单条目无效：{path}")
        if not (
            path.parts[:2] == ("config", "live-calibration")
            or path.parts[:3] == ("tests", "fixtures", "core_cases")
        ):
            raise ValueError(f"私有素材路径不在允许目录：{path}")
        seen.add(path)
        entries.append((path, digest))
    if not entries:
        raise ValueError("私有素材清单为空")
    return entries


def _safe_path(root: Path, relative: Path) -> Path:
    resolved_root = root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"素材路径越界：{relative}")
    return resolved


def _verified_files(root: Path, entries: list[tuple[Path, str]]) -> list[tuple[Path, Path]]:
    ready: list[tuple[Path, Path]] = []
    problems: list[str] = []
    for relative, expected in entries:
        path = _safe_path(root, relative)
        if not path.is_file():
            problems.append(f"缺少：{relative}")
        elif _sha256(path) != expected:
            problems.append(f"SHA-256 不匹配：{relative}")
        else:
            ready.append((relative, path))
    if problems:
        detail = "\n".join(problems[:20])
        raise ValueError(f"私有素材校验失败（共 {len(problems)} 项）：\n{detail}")
    return ready


def install(source_root: Path, entries: list[tuple[Path, str]]) -> int:
    if not source_root.is_dir():
        raise ValueError(f"本地素材目录不存在：{source_root}")
    verified = _verified_files(source_root, entries)
    copied = 0
    for relative, source in verified:
        dest = _safe_path(REPOSITORY_ROOT, relative)
        if dest.is_file() and _sha256(dest) == dict(entries)[relative]:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".asset-", delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            shutil.copyfile(source, temp_path)
            os.replace(temp_path, dest)
        finally:
            temp_path.unlink(missing_ok=True)
        copied += 1
    _verified_files(REPOSITORY_ROOT, entries)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--source", type=Path, help="包含清单相对路径的本地素材包目录")
    action.add_argument("--check", action="store_true", help="校验已安装素材，不复制文件")
    args = parser.parse_args()
    try:
        entries = _entries()
        if args.source is not None:
            copied = install(args.source, entries)
            print(f"已校验 {len(entries)} 张截图，安装 {copied} 张。")
        else:
            _verified_files(REPOSITORY_ROOT, entries)
            print(f"已校验 {len(entries)} 张本地截图。")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(2, f"[ERROR] {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
