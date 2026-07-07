"""规划/导入后主动释放临时对象（单机省内存）。"""
from __future__ import annotations

import gc
from typing import List, Optional


def release_planning_temp(
    trim_log: Optional[List[str]] = None,
    log_keep: int = 200,
) -> None:
    """裁剪规划日志长度并触发 gc（勿清空仍用于 HTTP 响应的大 dict）。"""
    if trim_log is not None and len(trim_log) > log_keep:
        del trim_log[:-log_keep]
    gc.collect()