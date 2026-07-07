"""
单机运行时：规划任务互斥 + 线程池规模（按 CPU 核数，避免多路全网规划占满内存）。
"""
from __future__ import annotations

import os
import threading
from typing import Optional

_plan_lock = threading.Lock()
_plan_lock_holder: Optional[str] = None


def planning_executor_max_workers() -> int:
    n = os.cpu_count() or 4
    return max(1, min(n, 4))


def try_acquire_plan_lock(label: str) -> bool:
    global _plan_lock_holder
    if not _plan_lock.acquire(blocking=False):
        return False
    _plan_lock_holder = label
    return True


def release_plan_lock() -> None:
    global _plan_lock_holder
    _plan_lock_holder = None
    try:
        _plan_lock.release()
    except RuntimeError:
        pass


def plan_lock_busy_detail() -> str:
    return _plan_lock_holder or "unknown"