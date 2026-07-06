"""
开发与 PyInstaller 打包后的路径解析。

- 运行时根目录：exe 所在目录（或开发时项目根），用于 license、config、data/、temp/
- 资源目录：onefile 解压目录 _MEIPASS（或开发时项目根），用于内嵌的 frontend 等
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def runtime_root() -> Path:
    """可写数据与优先读取的配置/许可文件所在目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Optional[Path]:
    """PyInstaller 内嵌资源目录；开发模式返回项目根。"""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    if not is_frozen():
        return Path(__file__).resolve().parent.parent
    return None


def resolve_data_path(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return runtime_root() / p


def resolve_resource(rel: str) -> Path:
    """先查运行时根目录，再查内嵌资源（便于用户用同目录文件覆盖打包内嵌）。"""
    name = rel.lstrip("/\\")
    at_runtime = runtime_root() / name
    if at_runtime.is_file() or at_runtime.is_dir():
        return at_runtime
    br = bundle_root()
    if br is not None:
        bundled = br / name
        if bundled.is_file() or bundled.is_dir():
            return bundled
    return at_runtime