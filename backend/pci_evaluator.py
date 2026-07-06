"""
统一 PCI 评估器 — PCI 冲突评分的唯一权威

替代散落在 pci_sss_constraints._pick_sss_group / site_planner._run_pci_planning
/ pci_planner.greedy_allocate 各自一套的 mod3/mod30/同PCI 评分逻辑。

设计目标:
  1. 单一连续评分公式: 同PCI/mod3/mod30 距离衰减合并到一个加权求和,
     避免阶跃硬切 (阈值边缘的小数扰动会显著改变结果)。
  2. 频段自适应阈值: safe_dist_km 与 same_pci_min_km 按 (plan_site_type, freq_band)
     查表, 700M 与 26GHz 不共用同一组阈值。
  3. 方向性背向作为软系数: 默认 0.5 (惩罚减半), 而非 0 (完全豁免)。
     保留对旁瓣的保守评估, 但允许窄数解。
  4. 评分越大越差: 0 = 无冲突, 1+ = 严重冲突。

调用方:
  - pci_sss_constraints.preassign_same_site_sss / _pick_sss_group
  - site_planner._run_pci_planning (SSS 锁定 + 非 SSS 分支)
  - pci_planner.greedy_allocate (复用评分 + 保留 forbidden 硬切)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from geo_utils import mutual_back_facing, vincenty_distance


# ─────────────────────────────────────────────────────────────────────────
# 频段 × 站型 自适应阈值表
#
# 来源依据:
#   - 700M (N28, 703-803MHz): 绕射强, 覆盖半径 ~5km, 同PCI 复用距离需 30km+
#   - 900M (Band 8): 同 700M, 略弱
#   - 1.8G (Band 3): 城区主流, 覆盖半径 3-5km, 同PCI 25km
#   - 2.1G (Band 1): 与 1.8G 类似
#   - 2.6G (Band 38/n41): 覆盖半径 1-2km, 同PCI 15km
#   - 3.5G (n78): 覆盖半径 700m-1km, 同PCI 10km
#   - 4.9G (n79): 同 3.5G
#   - 微站: 覆盖半径 200m, 同PCI 5km
#   - 室分: 覆盖半径 100m, 同PCI 1km
# ─────────────────────────────────────────────────────────────────────────

# 标准化频段别名 (与 sector_params.py / data_parser.py 保持一致)
FREQ_ALIASES = {
    "700M": "700M", "N28": "700M", "B28": "700M", "Band28": "700M",
    "900M": "FDD900", "FDD900": "FDD900", "Band8": "FDD900", "B8": "FDD900",
    "1.8G": "FDD1800", "FDD1800": "FDD1800", "Band3": "FDD1800", "B3": "FDD1800", "F": "FDD1800",
    "2.1G": "FDD2100", "FDD2100": "FDD2100", "Band1": "FDD2100", "B1": "FDD2100", "D": "FDD2100",
    "2.3G": "TDD2300", "TDD2300": "TDD2300", "Band40": "TDD2300", "B40": "TDD2300", "E": "TDD2300",
    "2.6G": "TDD2600", "TDD2600": "TDD2600", "Band38": "TDD2600", "B38": "TDD2600",
    "Band41": "TDD2600", "n41": "TDD2600", "A": "TDD2600",
    "3.5G": "TDD3500", "TDD3500": "TDD3500", "Band42": "TDD3500", "B42": "TDD3500",
    "n78": "TDD3500", "C": "TDD3500",
    "4.9G": "TDD4900", "TDD4900": "TDD4900", "n79": "TDD4900", "Band77": "TDD4900",
    "Band78": "TDD3500",
}


def normalize_freq_band(freq_band: Optional[str]) -> str:
    if not freq_band:
        return "UNKNOWN"
    s = str(freq_band).strip().upper()
    if s in FREQ_ALIASES:
        return FREQ_ALIASES[s]
    for k, v in FREQ_ALIASES.items():
        if k.upper() == s or k.lower() == str(freq_band).lower():
            return v
    return s  # 未知频段: 原样返回 (便于 fallback)


# 阈值表: (plan_site_type, freq_band_norm) -> (safe_dist_km, same_pci_min_km)
_THRESHOLDS: Dict[Tuple[str, str], Tuple[float, float]] = {
    ("macro", "700M"):       (5.0, 30.0),
    ("macro", "FDD900"):     (5.0, 30.0),
    ("macro", "FDD1800"):    (3.0, 25.0),
    ("macro", "FDD2100"):    (3.0, 25.0),
    ("macro", "TDD2300"):    (3.0, 25.0),
    ("macro", "TDD2600"):    (1.5, 15.0),
    ("macro", "TDD3500"):    (1.0, 10.0),
    ("macro", "TDD4900"):    (1.0, 10.0),
    ("micro", "700M"):       (0.3, 6.0),
    ("micro", "FDD900"):     (0.3, 6.0),
    ("micro", "FDD1800"):    (0.2, 5.0),
    ("micro", "TDD2600"):    (0.2, 4.0),
    ("micro", "TDD3500"):    (0.15, 3.0),
    ("indoor", "700M"):      (0.05, 1.0),
    ("indoor", "FDD900"):    (0.05, 1.0),
    ("indoor", "FDD1800"):   (0.05, 1.0),
    ("indoor", "TDD2600"):   (0.05, 1.0),
}

# 站型通配 fallback (频段未在表中时使用)
_TYPE_FALLBACK: Dict[str, Tuple[float, float]] = {
    "macro": (3.0, 25.0),
    "micro": (0.2, 5.0),
    "indoor": (0.05, 1.0),
}
_GLOBAL_FALLBACK: Tuple[float, float] = (1.5, 30.0)


def get_thresholds(plan_site_type: Optional[str],
                   freq_band: Optional[str]) -> Tuple[float, float]:
    """
    返回 (safe_dist_km, same_pci_min_km).
    优先级: 精确匹配 > 站型通配 > 全局默认.
    """
    pst = (plan_site_type or "macro").lower()
    fb = normalize_freq_band(freq_band)
    key = (pst, fb)
    if key in _THRESHOLDS:
        return _THRESHOLDS[key]
    if pst in _TYPE_FALLBACK:
        return _TYPE_FALLBACK[pst]
    return _GLOBAL_FALLBACK


# ─────────────────────────────────────────────────────────────────────────
# 评估器
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class PciEvaluator:
    """
    PCI 冲突连续评分器.

    评分公式 (per neighbor):
        penalty = w_pci  * smoothstep(same_pci_min_km, dist)
                + w_mod3 * smoothstep(2·safe_dist_km, dist)   · [mod3 match]
                + w_mod30 * smoothstep(0.5·same_pci_min_km, dist) · [mod30 match & NR]
      其中 smoothstep(threshold, dist) = clamp(1 - dist/threshold, 0, 1) ** 2
      (二阶衰减: 阈值边缘为 0, 距离 0 时为 1, 中点为 0.25)

    距离越近 → penalty 越大 → 越不优.
    双向背向 → 整组 penalty 乘 0.5 (软豁免, 仍保留少量惩罚).

    score_cell(target_pci, neighbor) -> 单 cell 单 neighbor 的 penalty
    score_group(target_pcis, neighbors) -> 整组最差扇区 penalty
    hard_violations(target_pci, neighbor) -> 硬冲突 (距离 < safe_dist 同mod3 或同PCI < same_pci_min)
    """

    safe_dist_km: float = 1.5
    same_pci_min_km: float = 30.0
    check_mod30: bool = True
    directional_filter: bool = True

    # 权重 (内部默认值, 经验值; 调整请通过 __init__)
    w_pci: float = 1.0
    w_mod3: float = 0.6
    w_mod30: float = 0.4

    @classmethod
    def from_cell(cls, cell: Dict[str, Any],
                  check_mod30: bool = True,
                  directional_filter: bool = True) -> "PciEvaluator":
        safe_km, same_pci_min = get_thresholds(
            cell.get("plan_site_type") or cell.get("site_type"),
            cell.get("freq_band") or cell.get("freq_band_raw"),
        )
        return cls(
            safe_dist_km=safe_km,
            same_pci_min_km=same_pci_min,
            check_mod30=check_mod30,
            directional_filter=directional_filter,
        )

    def smoothstep(self, threshold_km: float, dist_km: float) -> float:
        """平滑衰减: 距离 >= 阈值 -> 0; 距离 = 0 -> 1; 介于之间为 1 - (d/t)^2"""
        if threshold_km <= 0 or dist_km >= threshold_km:
            return 0.0
        if dist_km <= 0:
            return 1.0
        ratio = dist_km / threshold_km
        return (1.0 - ratio * ratio)

    def neighbor_penalty(self,
                         target_pci: int,
                         neighbor_pci: int,
                         dist_km: float,
                         back_facing_factor: float = 1.0) -> float:
        """
        单 target_pci 单 neighbor 的 penalty.
        back_facing_factor: 1.0 = 正向, 0.5 = 双向背向软豁免, 0.0 = 完全豁免 (默认 1.0)
        """
        if target_pci is None or neighbor_pci is None:
            return 0.0
        if dist_km is None or dist_km < 0:
            return 0.0
        # 同 PCI: same_pci_min_km 内禁止 (实则是 PCI 碰撞)
        same_pci = (int(target_pci) == int(neighbor_pci))
        same_mod3 = (int(target_pci) % 3 == int(neighbor_pci) % 3)
        same_mod30 = self.check_mod30 and (int(target_pci) % 30 == int(neighbor_pci) % 30)

        penalty = 0.0
        if same_pci:
            penalty += self.w_pci * self.smoothstep(self.same_pci_min_km, dist_km)
        # mod30 优先于 mod3 (包含 mod3 信息)
        if same_mod30:
            penalty += self.w_mod30 * self.smoothstep(
                0.5 * self.same_pci_min_km, dist_km
            )
        elif same_mod3:
            penalty += self.w_mod3 * self.smoothstep(
                2.0 * self.safe_dist_km, dist_km
            )
        return penalty * back_facing_factor

    def score_cell(self,
                   target_pci: int,
                   target_cell: Dict[str, Any],
                   neighbors: Sequence[Tuple[int, Dict[str, Any], float]]) -> float:
        """
        评估 target_pci 在 target_cell 位置上的冲突评分.
        neighbors: [(neighbor_pci, neighbor_cell_dict, distance_km), ...]

        返回 Σ penalty (越大越差). 含方向背向软豁免.
        """
        total = 0.0
        for nbr_pci, nbr_cell, d_km in neighbors:
            back_factor = 1.0
            if self.directional_filter and d_km is not None and d_km >= self.safe_dist_km:
                if mutual_back_facing(target_cell, nbr_cell):
                    back_factor = 0.5  # 双向背向: 惩罚减半 (但非完全豁免)
            total += self.neighbor_penalty(target_pci, nbr_pci, d_km, back_factor)
        return total

    def score_group(self,
                    target_pcis: Sequence[int],
                    target_cells: Sequence[Dict[str, Any]],
                    neighbors: Sequence[Tuple[int, Dict[str, Any], float]],
                    neighbors_simple: Optional[Sequence[Tuple[int, float]]] = None) -> float:
        """
        评估整组 PCI (同站 N 个扇区):
        返回 max(per_cell_score) — 最差扇区定胜负.
        备选: 返回 mean — 平摊; 这里用 max 因为只要一个扇区撞到 mod3 就失败.

        支持两种邻居列表格式:
          - neighbors: (pci, cell_dict, dist_km) 三元组 — 全功能 (方向/距离)
          - neighbors_simple: (pci, dist_km) 二元组 — 兼容老接口 (无方向信息)
        """
        if not target_pcis:
            return 0.0

        if neighbors is not None:
            per_cell = []
            for pci, cell in zip(target_pcis, target_cells):
                per_cell.append(self.score_cell(pci, cell, neighbors))
            return max(per_cell) if per_cell else 0.0

        if neighbors_simple is not None:
            # 用默认占位 cell (无方向性豁免), 但保留距离评分
            placeholder_nbrs = [(int(p), {"lat": 0, "lon": 0, "azimuth": None,
                                            "beamwidth": 360}, float(d))
                                for p, d in neighbors_simple]
            per_cell = []
            for pci, cell in zip(target_pcis, target_cells):
                per_cell.append(self.score_cell(pci, cell, placeholder_nbrs))
            return max(per_cell) if per_cell else 0.0

        return 0.0

    def hard_violations(self,
                        target_pci: int,
                        target_cell: Dict[str, Any],
                        neighbors: Sequence[Tuple[int, Dict[str, Any], float]]) -> List[str]:
        """
        列出硬冲突描述 (供 forbidden 集/告警).
        硬冲突定义:
          - 同 PCI & dist < same_pci_min_km (且非双向背向)
          - 同 mod3 & dist < safe_dist_km (同站物理)
          - 同 mod30 & dist < 0.5 * same_pci_min_km (NR)
        """
        violations = []
        for nbr_pci, nbr_cell, d_km in neighbors:
            back = (self.directional_filter
                    and d_km is not None
                    and d_km >= self.safe_dist_km
                    and mutual_back_facing(target_cell, nbr_cell))
            if back:
                continue
            same_pci = int(target_pci) == int(nbr_pci)
            same_mod3 = int(target_pci) % 3 == int(nbr_pci) % 3
            same_mod30 = self.check_mod30 and (int(target_pci) % 30 == int(nbr_pci) % 30)

            if same_pci and d_km < self.same_pci_min_km:
                violations.append(
                    f"PCI collision {target_pci} @ {d_km:.2f}km (limit {self.same_pci_min_km}km)"
                )
            if same_mod3 and d_km < self.safe_dist_km:
                violations.append(
                    f"mod3 collision {target_pci}%3={target_pci%3} "
                    f"vs {nbr_pci}%3={nbr_pci%3} @ {d_km:.2f}km "
                    f"(limit {self.safe_dist_km}km)"
                )
            if same_mod30 and d_km < 0.5 * self.same_pci_min_km:
                violations.append(
                    f"mod30 collision {target_pci}%30={target_pci%30} "
                    f"vs {nbr_pci}%30={nbr_pci%30} @ {d_km:.2f}km "
                    f"(limit {0.5*self.same_pci_min_km}km)"
                )
        return violations

    def build_neighbor_list(self,
                            target_cells: Sequence[Dict[str, Any]],
                            existing_pci: Dict[str, Tuple[int, float, float]],
                            existing_cells_index: Dict[str, Dict[str, Any]],
                            ) -> List[Tuple[int, Dict[str, Any], float]]:
        """
        构造 (pci, cell_dict, dist_km) 邻居列表 — 从 target_cells 到 existing_pci 中所有邻居.
        用于 score_cell / hard_violations 调用.
        距离阈值 = max(2 * safe_dist_km, same_pci_min_km) (覆盖全部冲突范围)
        """
        max_radius = max(2.0 * self.safe_dist_km, self.same_pci_min_km)
        neighbors: List[Tuple[int, Dict[str, Any], float]] = []
        for tgt in target_cells:
            tlat, tlon = tgt.get("lat"), tgt.get("lon")
            if tlat is None or tlon is None:
                continue
            for ecgi, (p, olat, olon) in existing_pci.items():
                if p is None or int(p) < 0:
                    continue
                if olat is None or olon is None:
                    continue
                try:
                    d_km = vincenty_distance(
                        float(tlat), float(tlon),
                        float(olat), float(olon)
                    ) / 1000.0
                except Exception:
                    continue
                if d_km > max_radius:
                    continue
                cell = existing_cells_index.get(ecgi)
                if cell is None:
                    cell = {"lat": olat, "lon": olon}
                neighbors.append((int(p), cell, d_km))
        return neighbors

    def score_neighbors_simple(self,
                               target_pci: int,
                               target_cell: Dict[str, Any],
                               neighbors_simple: Sequence[Tuple[int, float]],
                               cell_factory=None) -> float:
        """
        兼容 (pci, dist_km) 二元组邻居列表 — 给 pci_planner 等老接口用.
        cell_factory: 可选, 输入 (pci, dist_km) 返回 cell dict (用于方向性判定)
                      默认返回 None-方位占位 (不会触发方向性豁免)
        """
        eval_neighbors = []
        for pci, d in neighbors_simple:
            cell = cell_factory(pci, d) if cell_factory else None
            eval_neighbors.append((int(pci), cell if cell else {"lat": 0, "lon": 0,
                                                              "azimuth": None,
                                                              "beamwidth": 360}, float(d)))
        return self.score_cell(int(target_pci), target_cell, eval_neighbors)


# ─────────────────────────────────────────────────────────────────────────
# 便捷函数 (供其他模块无需构造 evaluator 直接调用)
# ─────────────────────────────────────────────────────────────────────────

def pick_best_nid1(nid1_max: int,
                   n_sectors: int,
                   neighbors: Sequence[Tuple[int, Dict[str, Any], float]],
                   target_cells_template: Sequence[Dict[str, Any]],
                   evaluator: PciEvaluator,
                   tie_break_seed: Optional[int] = None) -> int:
    """
    在 [0, nid1_max] 范围内挑选最佳 nid1 (整组 N 个 PCI = nid1*3+0/1/2/...).
    评分: evaluator.score_group (越低越好).
    """
    import random
    if tie_break_seed is not None:
        random.seed(tie_break_seed)

    best_nid1 = 0
    best_score = float("inf")
    for nid1_try in range(nid1_max + 1):
        target_pcis = [nid1_try * 3 + (j % 3) for j in range(n_sectors)]
        score = evaluator.score_group(target_pcis, target_cells_template, neighbors)
        # 同分时随机扰动打破
        score += random.random() * 1e-9
        if score < best_score:
            best_score = score
            best_nid1 = nid1_try
    return best_nid1