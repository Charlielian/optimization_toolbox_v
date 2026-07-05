"""
配置加载模块
从项目根目录的 config.yaml 读取配置，支持默认值。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

__all__ = ["config", "load_config"]

# 项目根目录
ROOT_DIR = Path(__file__).parent.parent
CONFIG_FILE = ROOT_DIR / "config.yaml"


def _default_config() -> Dict[str, Any]:
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 4001,
        },
        "paths": {
            "frontend_dir": "frontend",
            "temp_dir": "temp",
        },
        "cors": {
            "allow_origins": ["*"],
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        },
        "logging": {
            "level": "INFO",
        },
        "license": {
            "enabled": True,
            "file": "license.lic",
        },
    }


def load_config() -> Dict[str, Any]:
    defaults = _default_config()

    if not CONFIG_FILE.exists():
        return defaults

    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}

        # 深度合并：用户配置覆盖默认值
        def merge(base: Dict, overlay: Dict) -> Dict:
            result = base.copy()
            for key, val in overlay.items():
                if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                    result[key] = merge(result[key], val)
                else:
                    result[key] = val
            return result

        return merge(defaults, user_cfg)

    except Exception:
        return defaults


class Config:
    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = cfg

    # ── server ──
    @property
    def server_host(self) -> str:
        return self._cfg.get("server", {}).get("host", "127.0.0.1")

    @property
    def server_port(self) -> int:
        return self._cfg.get("server", {}).get("port", 4001)

    # ── paths ──
    @property
    def frontend_dir(self) -> Path:
        sub = self._cfg.get("paths", {}).get("frontend_dir", "frontend")
        return ROOT_DIR / sub

    @property
    def temp_dir(self) -> Path:
        sub = self._cfg.get("paths", {}).get("temp_dir", "temp")
        return ROOT_DIR / sub

    # ── cors ──
    @property
    def cors_allow_origins(self) -> List[str]:
        return self._cfg.get("cors", {}).get("allow_origins", ["*"])

    @property
    def cors_allow_methods(self) -> List[str]:
        return self._cfg.get("cors", {}).get("allow_methods", ["*"])

    @property
    def cors_allow_headers(self) -> List[str]:
        return self._cfg.get("cors", {}).get("allow_headers", ["*"])

    # ── logging ──
    @property
    def log_level(self) -> str:
        return self._cfg.get("logging", {}).get("level", "INFO")

    @property
    def license_enabled(self) -> bool:
        return bool(self._cfg.get("license", {}).get("enabled", True))

    @property
    def license_file(self) -> str:
        return str(self._cfg.get("license", {}).get("file", "license.lic"))

    @property
    def raw(self) -> Dict[str, Any]:
        return self._cfg


config = Config(load_config())
