"""
日志配置
- 日志目录: <项目根>/logs/
- 单文件最大 40MB, 保留 10 个备份 (app.log, app.log.1, app.log.2 ...)
- 控制台 + 文件双输出
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


# 项目根 (backend 的父目录)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOGS_DIR / "app.log"

MAX_BYTES = 40 * 1024 * 1024   # 40 MB
BACKUP_COUNT = 10

# 日志格式: 时间 | 级别 | 模块 | 消息
_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """初始化全局日志, 幂等可重复调用"""
    global _configured
    if _configured:
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # 清理已有 handler, 避免重复 (uvicorn reload 时尤其重要)
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(_FMT, _DATEFMT)

    # 文件 handler (按大小轮转)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    # 控制台 handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(level)
    root.addHandler(console)

    # 让 uvicorn / fastapi 的日志也走我们的 handler
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(level)

    _configured = True
    logging.getLogger(__name__).info(
        "日志初始化完成: %s (max=%dMB, backups=%d)",
        LOG_FILE, MAX_BYTES // 1024 // 1024, BACKUP_COUNT,
    )


def get_logger(name: str) -> logging.Logger:
    """便捷获取 logger"""
    return logging.getLogger(name)
