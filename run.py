"""
网优百宝箱 - 打包入口
适配 PyInstaller，自动检测 _MEIPASS 并启动服务
"""
import os
import sys
import time
import threading
import webbrowser
from pathlib import Path

# ── 1. 确定项目根目录 ──
if getattr(sys, "frozen", False):
    # PyInstaller onedir: exe 所在目录即为项目根目录（数据文件平级放置）
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    # 源码运行: 本文件所在目录
    BASE_DIR = Path(__file__).parent.resolve()

os.chdir(BASE_DIR)

# ── 2. 把 backend 加入路径 ──
BACKEND_DIR = BASE_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# ── 3. Monkey-patch config.py 的 ROOT_DIR ──
# 让 config 和 main 中的 ROOT_DIR 指向正确位置
import config as _config_module
_config_module.ROOT_DIR = BASE_DIR
_config_module.CONFIG_FILE = BASE_DIR / "config.yaml"

# 重新创建 config 实例，使 frontend_dir / temp_dir 基于新的 ROOT_DIR
_config_module.config = _config_module.Config(_config_module.load_config())

# 同步 patch main.py 导入后使用的 ROOT_DIR
import main as _main_module
_main_module.ROOT_DIR = BASE_DIR
_main_module.config = _config_module.config

# 重新计算 BATCH_RESULT_DIR
_main_module.BATCH_RESULT_DIR = _config_module.config.temp_dir / "batch_results"
_main_module.BATCH_RESULT_DIR.mkdir(exist_ok=True)

# ── 4. 启动 uvicorn ──
import uvicorn
from main import app

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "4001"))
URL = f"http://{HOST}:{PORT}"


def _open_browser():
    time.sleep(1.5)
    webbrowser.open(URL)


if __name__ == "__main__":
    print("=" * 50)
    print(" 网优百宝箱 v1.2.1")
    print(f" 服务地址: {URL}")
    print("=" * 50)
    print("")

    # 首次运行自动打开浏览器
    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=HOST, port=PORT)
