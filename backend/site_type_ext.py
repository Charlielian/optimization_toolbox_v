"""
站点类型扩展 (macro / micro / indoor)

- 与 backend/sector_params.py 的 normalize_site_type 并存
- 提供 PCI 规划距离阈值映射
- 提供 to_plan_site_type() 字符串 → 三分类
- 提供 get_pci_distance_thresholds() → (safe_m, same_pci_min_m)
- 提供 get_plan_label() 中文标签
"""
from __future__ import annotations

from typing import Optional, Tuple


# ──────────────────────────────────────────────
# 站点类型 → PCI 距离阈值映射 (与 PCI 规则一致)
#   宏站: 700m / 5km
#   微站: 200m / 3km
#   室分: 100m / 2km
# ──────────────────────────────────────────────
PLAN_SITE_TYPE_RULES: dict = {
    "macro": {
        "label": "宏站",
        "scene": "outdoor",          # 对应 sector_params 中的归一化值
        "pci_safe_distance_m": 700,
        "pci_same_pci_min_m": 5000,
    },
    "micro": {
        "label": "微站",
        "scene": "outdoor",
        "pci_safe_distance_m": 200,
        "pci_same_pci_min_m": 3000,
    },
    "indoor": {
        "label": "室分",
        "scene": "indoor",
        "pci_safe_distance_m": 100,
        "pci_same_pci_min_m": 2000,
    },
}

VALID_PLAN_SITE_TYPES = tuple(PLAN_SITE_TYPE_RULES.keys())


def to_plan_site_type(site_type_raw: Optional[str]) -> str:
    """
    把用户/工参里的站点类型字符串归一化为 macro / micro / indoor
    默认: 宏站 (陆地 → macro)
    """
    if not site_type_raw:
        return "macro"
    s = str(site_type_raw).strip()
    if not s:
        return "macro"
    # 室分优先
    if any(k in s for k in ["室", "INDOOR", "Indoor", "indoor", "分布", "indoor"]):
        return "indoor"
    # 微站 (新增)
    if any(k in s for k in ["微", "MICRO", "micro", "Micro", "Small Cell", "small_cell", "SmallCell"]):
        return "micro"
    # 默认 (陆地/海/其他 → 宏站)
    return "macro"


def get_plan_label(plan_site_type: str) -> str:
    """返回中文标签"""
    return PLAN_SITE_TYPE_RULES.get(plan_site_type, PLAN_SITE_TYPE_RULES["macro"])["label"]


def get_scene(plan_site_type: str) -> str:
    """返回 sector_params 中的 scene (outdoor / indoor / offshore)"""
    return PLAN_SITE_TYPE_RULES.get(plan_site_type, PLAN_SITE_TYPE_RULES["macro"])["scene"]


def get_pci_distance_thresholds(plan_site_type: str) -> Tuple[float, float]:
    """
    返回 (safe_distance_m, same_pci_min_m)
    - safe_distance_m: 强制 mod3/mod30 不同的最小距离
    - same_pci_min_m: 同 PCI 复用的最小距离
    """
    rule = PLAN_SITE_TYPE_RULES.get(plan_site_type, PLAN_SITE_TYPE_RULES["macro"])
    return float(rule["pci_safe_distance_m"]), float(rule["pci_same_pci_min_m"])


def build_per_site_thresholds(cells: list, default_safe_m: float = 1500.0,
                              default_same_pci_min_m: float = 30000.0) -> dict:
    """
    遍历 cells, 按 cell['plan_site_type'] 查表,
    与全局默认值取并集(更严者) → 返回 {ecgi: (safe_m, same_pci_min_m)}

    取并集逻辑:
        safe_distance_m = min(plan_site_type阈值, default)
        same_pci_min_m = min(plan_site_type阈值, default)
    """
    out: dict = {}
    for c in cells:
        ecgi = c.get("ecgi")
        if not ecgi:
            continue
        pst = c.get("plan_site_type") or "macro"
        safe, same = get_pci_distance_thresholds(pst)
        # 并集 (取更严/更小)
        final_safe = min(safe, default_safe_m)
        final_same = min(same, default_same_pci_min_m)
        out[ecgi] = (final_safe, final_same)
    return out
