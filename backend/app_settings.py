"""
规划相关默认参数（持久化到 SQLite meta 表，重启后仍有效）
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from db import get_meta_json, set_meta_json

META_KEY = "app_plan_defaults"

# 内置默认值（数据库无记录时使用）
_BUILTIN: Dict[str, Any] = {
    "pci": {
        "engine": "legacy",
        "reuse_distance_km": 5.0,
        "check_mod6": False,
        "check_mod30": True,
        "directional_filter": True,
        "use_beam_overlap_score": False,
    },
    "neighbor": {
        "max_neighbors": 16,
        "max_distance_km": 5.0,
        "weight_distance": 0.7,
        "weight_overlap": 0.3,
        "score_threshold": 0.5,
        "single_score_threshold": 0.5,
        "enable_cross_system": True,
        "enable_bidirectional": True,
    },
    "batch": {
        "default_nbr_score_threshold": 0.5,
        "planning_mode": "pci+nbr",
    },
}

_cache: Optional[Dict[str, Any]] = None


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_plan_defaults() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return copy.deepcopy(_cache)
    stored = get_meta_json(META_KEY)
    if stored and isinstance(stored, dict):
        _cache = _deep_merge(_BUILTIN, stored)
    else:
        _cache = copy.deepcopy(_BUILTIN)
    return copy.deepcopy(_cache)


def save_plan_defaults(patch: Dict[str, Any]) -> Dict[str, Any]:
    """合并保存并写库"""
    global _cache
    current = get_plan_defaults()
    merged = _deep_merge(current, patch or {})
    _validate(merged)
    set_meta_json(META_KEY, merged)
    _cache = merged
    return copy.deepcopy(merged)


def reload_plan_defaults() -> Dict[str, Any]:
    global _cache
    _cache = None
    return get_plan_defaults()


def reset_plan_defaults() -> Dict[str, Any]:
    global _cache
    _cache = copy.deepcopy(_BUILTIN)
    set_meta_json(META_KEY, _cache)
    return copy.deepcopy(_cache)


def _validate(d: Dict[str, Any]) -> None:
    pci = d.get("pci") or {}
    nbr = d.get("neighbor") or {}
    batch = d.get("batch") or {}
    r = float(pci.get("reuse_distance_km", 5))
    if not (0.1 <= r <= 100):
        raise ValueError("reuse_distance_km 须在 0.1~100")
    for label, section, field in (
        ("邻区得分阈值", "neighbor", "score_threshold"),
        ("单站邻区得分阈值", "neighbor", "single_score_threshold"),
        ("批量默认邻区得分阈值", "batch", "default_nbr_score_threshold"),
    ):
        v = float((d.get(section) or {}).get(field, 0.5))
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"{label} 须在 0~1")
    mn = int(nbr.get("max_neighbors", 16))
    if not (1 <= mn <= 800):
        raise ValueError("max_neighbors 须在 1~800")
    wd = float(nbr.get("weight_distance", 0.7))
    wo = float(nbr.get("weight_overlap", 0.3))
    if wd < 0 or wo < 0 or wd + wo <= 0:
        raise ValueError("距离/交叠权重须 ≥0 且之和 > 0")


def neighbor_kwargs_from_defaults(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """供 plan_neighbors / _run_neighbor_planning 使用的参数字典"""
    d = get_plan_defaults()
    nbr = d.get("neighbor") or {}
    kw = {
        "max_neighbors": int(nbr.get("max_neighbors", 16)),
        "max_distance_km": float(nbr.get("max_distance_km", 5.0)),
        "weight_distance": float(nbr.get("weight_distance", 0.7)),
        "weight_overlap": float(nbr.get("weight_overlap", 0.3)),
        "score_threshold": float(nbr.get("score_threshold", 0.5)),
        "enable_cross_system": bool(nbr.get("enable_cross_system", True)),
        "enable_bidirectional": bool(nbr.get("enable_bidirectional", True)),
    }
    if overrides:
        kw.update({k: v for k, v in overrides.items() if v is not None})
    return kw


def batch_default_nbr_score_threshold() -> float:
    d = get_plan_defaults()
    return float((d.get("batch") or {}).get("default_nbr_score_threshold", 0.5))


def single_site_default_score_threshold() -> float:
    d = get_plan_defaults()
    return float((d.get("neighbor") or {}).get("single_score_threshold", 0.5))


def pci_defaults_dict() -> Dict[str, Any]:
    """单站/批量 Form 未传时的 PCI 相关默认"""
    d = get_plan_defaults()
    pci = d.get("pci") or {}
    batch = d.get("batch") or {}
    return {
        "engine": str(pci.get("engine", "legacy")),
        "reuse_distance_km": float(pci.get("reuse_distance_km", 5.0)),
        "check_mod6": bool(pci.get("check_mod6", False)),
        "check_mod30": pci.get("check_mod30", True) is not False,
        "use_beam_overlap_score": bool(pci.get("use_beam_overlap_score", False)),
        "directional_filter": pci.get("directional_filter", True) is not False,
        "planning_mode": str(batch.get("planning_mode", "pci+nbr")),
    }