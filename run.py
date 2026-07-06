"""
网优百宝箱 - 打包入口
适配 PyInstaller onedir，exe 同目录放置 config.yaml / frontend / license.lic
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    BASE_DIR = Path(__file__).parent.resolve()

os.chdir(BASE_DIR)

BACKEND_DIR = BASE_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import config as _config_module

_config_module.ROOT_DIR = BASE_DIR
_config_module.CONFIG_FILE = BASE_DIR / "config.yaml"
_config_module.config = _config_module.Config(_config_module.load_config())

import license_check as _license_module

_license_module.ROOT_DIR = BASE_DIR

import main as _main_module

_main_module.ROOT_DIR = BASE_DIR
_main_module.config = _config_module.config
_main_module.BATCH_RESULT_DIR = _config_module.config.temp_dir / "batch_results"
_main_module.BATCH_RESULT_DIR.mkdir(parents=True, exist_ok=True)

import uvicorn
from main import app

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "4001"))
URL = f"http://{HOST}:{PORT}"


def _open_browser() -> None:
    time.sleep(1.5)
    webbrowser.open(URL)


if __name__ == "__main__":
    print("=" * 50)
    print(" 网优百宝箱")
    print(f" 服务地址: {URL}")
    print("=" * 50)
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT)