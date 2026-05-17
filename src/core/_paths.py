"""项目内资源路径查找：开发模式 + Nuitka standalone 双兼容。"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def find_app_icon() -> Path | None:
    """开发：<repo>/assets/icons/app_icon.ico
    Nuitka standalone：sys.executable 同目录（build_nuitka.bat 用
    --include-data-files 把 .ico 拷到 exe 旁边）"""
    candidates = [
        _REPO_ROOT / "assets" / "icons" / "app_icon.ico",
        Path(sys.executable).resolve().parent / "app_icon.ico",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def find_app_logo_png() -> Path | None:
    """关于页 hero 区用的 256px PNG。Nuitka 打包未必拷贝 PNG，找不到返回 None，
    调用方应回退到 .ico 或不显示 logo。"""
    candidates = [
        _REPO_ROOT / "assets" / "icons" / "app_icon_256.png",
        Path(sys.executable).resolve().parent / "app_icon_256.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None
