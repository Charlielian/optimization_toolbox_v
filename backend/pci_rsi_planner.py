"""
RFTools 风格 PCI 贪心分配引擎
移植自 https://github.com/mbebs/RFTools/blob/master/pci_rsi_planner_dialog.py

核心特性:
  1. 按 (tech, band) 分组, 组内独立 PCI 资源池
  2. 顺序递增 PCI (next_pci += 1, 溢出回卷)
  3. 空间索引 {pci: [(cell_id, point)]} → O(1) 复用距离查询
  4. Mod3 冲突: 2 × reuse_distance 内禁止
  5. Mod6 冲突: reuse_distance 内禁止 (RFTools 原始)
  6. Mod30 冲突(NR): reuse_distance 内禁止 (我们扩展)
  7. 锁定小区: locked=True 保留其 PCI, 不参与分配
  8. Fallback: 全耗尽时按 next 顺序取

与原 QGIS 实现的差异:
  - 距离从 QGIS QgsDistanceArea 改为 vincenty_distance (WGS84 椭球)
  - 几何从 QGIS 改为 (lat, lon) tuple
  - 字段名: tech→rat, band→freq_band, locked→locked 字段
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from geo_utils import mutual_back_facing, vincenty_distance


# PCI 资源池
PCI_POOL_4G = list(range(0, 504))
PCI_POOL_5G = list(range(0, 1008))


def _distance_km(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return vincenty_distance(a["lat"], a["lon"], b["lat"], b["lon"]) / 1000.0


def _cell_pool(rat: str) -> List[int]:
    return PCI_POOL_5G if rat == "NR" else PCI_POOL_4G


def _is_locked(c: Dict[str, Any]) -> bool:
    """兼容多种 locked 表示: bool / 1/0 / 'true'/'false' / '是'/'否'"""
    v = c.get("locked", False)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "locked", "是", "y", "t")
    return False


def _plan_group(
    feats: List[Dict[str, Any]],
    rat: str,
    pci_min: int,
    pci_max: int,
    reuse_distance_km: float,
    check_mod3: bool,
    check_mod6: bool,
    check_mod30: bool,
    directional_filter: bool = True,
) -> List[Dict[str, Any]]:
    """
    对一个 (tech, band) 组内的 cells 做 PCI 分配.
    移植自 RFTools pci_rsi_planner_dialog.py 的二层循环.

    directional_filter: 启用时, 双向背向(azimuth+beamwidth 判定)的小区对
                       豁免同PCI/mod3/mod6/mod30 距离冲突.

    返回: 写好 new_pci 的 cells (in-place 改 + 返回)
    """
    used_pcis: Set[int] = set()
    next_pci = pci_min

    # id(f) -> cell 引用, 方向性过滤需要 other 的 azimuth/beamwidth
    cells_by_id: Dict[int, Dict[str, Any]] = {}

    # ─── 第一遍: 锁定小区 ───
    for f in feats:
        if not _is_locked(f):
            continue
        pci_val = f.get("pci")
        if pci_val is None or int(pci_val) < 0:
            continue
        try:
            v = int(pci_val)
        except (ValueError, TypeError):
            continue
        f["new_pci"] = v
        used_pcis.add(v)
        cells_by_id[id(f)] = f

    # 空间索引: pci -> [(feat_id, point)]
    pci_spatial_index: Dict[int, List[Tuple[Any, Tuple[float, float]]]] = {}

    # 把 locked 已有 PCI 加入空间索引
    for f in feats:
        if f.get("new_pci") is None:
            continue
        try:
            v = int(f["new_pci"])
        except (ValueError, TypeError):
            continue
        if v not in pci_spatial_index:
            pci_spatial_index[v] = []
        pci_spatial_index[v].append((id(f), (f["lat"], f["lon"])))

    # ─── 第二遍: 顺序递增分配 ───
    for f in feats:
        if f.get("new_pci") is not None:
            # 锁定小区, 跳过
            continue
        has_valid_geom = (
            "lat" in f and "lon" in f
            and f["lat"] is not None and f["lon"] is not None
        )
        feat_point = (f["lat"], f["lon"]) if has_valid_geom else None

        candidate_pci = next_pci
        assigned = False
        attempts = 0
        max_attempts = pci_max - pci_min + 1

        while not assigned and attempts < max_attempts:
            if candidate_pci in used_pcis:
                candidate_pci += 1
                if candidate_pci > pci_max:
                    candidate_pci = pci_min
                attempts += 1
                continue

            # ── 复用距离检查 (同 PCI 内) ──
            reuse_ok = True
            if has_valid_geom and candidate_pci in pci_spatial_index:
                for other_id, other_point in pci_spatial_index[candidate_pci]:
                    d_m = vincenty_distance(
                        feat_point[0], feat_point[1],
                        other_point[0], other_point[1]
                    )
                    d_km = d_m / 1000.0
                    # 方向性豁免: 双向背向则忽略此对
                    if directional_filter:
                        other_cell = cells_by_id.get(other_id)
                        if other_cell is not None and mutual_back_facing(f, other_cell):
                            continue
                    if d_km < reuse_distance_km:
                        reuse_ok = False
                        break

            # ── Mod3 / Mod6 / Mod30 冲突检查 ──
            mod_ok = True
            if has_valid_geom and (check_mod3 or check_mod6 or (rat == "NR" and check_mod30)):
                candidate_mod3 = candidate_pci % 3
                candidate_mod6 = candidate_pci % 6
                candidate_mod30 = candidate_pci % 30

                for other_pci in list(pci_spatial_index.keys()):
                    other_mod3 = other_pci % 3
                    other_mod6 = other_pci % 6
                    other_mod30 = other_pci % 30

                    has_mod3_conflict = (candidate_mod3 == other_mod3)
                    has_mod6_conflict = (candidate_mod6 == other_mod6)
                    has_mod30_conflict = (rat == "NR" and check_mod30 and (candidate_mod30 == other_mod30))

                    if not (has_mod3_conflict or has_mod6_conflict or has_mod30_conflict):
                        continue

                    for other_id, other_point in pci_spatial_index[other_pci]:
                        d_m = vincenty_distance(
                            feat_point[0], feat_point[1],
                            other_point[0], other_point[1]
                        )
                        d_km = d_m / 1000.0

                        # 方向性豁免: 双向背向则忽略此对
                        if directional_filter:
                            other_cell = cells_by_id.get(other_id)
                            if other_cell is not None and mutual_back_facing(f, other_cell):
                                continue

                        if check_mod3 and has_mod3_conflict and d_km < (reuse_distance_km * 2):
                            mod_ok = False
                            break
                        if check_mod6 and has_mod6_conflict and d_km < reuse_distance_km:
                            mod_ok = False
                            break
                        if has_mod30_conflict and d_km < reuse_distance_km:
                            mod_ok = False
                            break
                    if not mod_ok:
                        break

            if reuse_ok and mod_ok:
                used_pcis.add(candidate_pci)
                f["new_pci"] = int(candidate_pci)
                if candidate_pci not in pci_spatial_index:
                    pci_spatial_index[candidate_pci] = []
                if has_valid_geom:
                    pci_spatial_index[candidate_pci].append((id(f), feat_point))
                    cells_by_id[id(f)] = f
                assigned = True
                next_pci = candidate_pci + 1
                if next_pci > pci_max:
                    next_pci = pci_min
            else:
                candidate_pci += 1
                if candidate_pci > pci_max:
                    candidate_pci = pci_min
                attempts += 1

        # Fallback: 全耗尽
        if not assigned:
            fallback_pci = next_pci
            fb_attempts = 0
            max_fb = pci_max - pci_min + 1
            while fallback_pci in used_pcis and fb_attempts < max_fb:
                fallback_pci += 1
                if fallback_pci > pci_max:
                    fallback_pci = pci_min
                fb_attempts += 1
            if fallback_pci in used_pcis:
                # 资源池用尽, 强制使用 next
                fallback_pci = next_pci
            used_pcis.add(fallback_pci)
            f["new_pci"] = int(fallback_pci)
            if fallback_pci not in pci_spatial_index:
                pci_spatial_index[fallback_pci] = []
            if has_valid_geom:
                pci_spatial_index[fallback_pci].append((id(f), feat_point))
                cells_by_id[id(f)] = f
            next_pci = fallback_pci + 1
            if next_pci > pci_max:
                next_pci = pci_min

    # ── 硬性 post-check: 同站 mod3 必须各占一位 (rftools 也是逐扇区贪心) ──
    from pci_sss_constraints import _check_mod3_only
    _check_mod3_only(feats, pci_field="new_pci", group_by="auto")

    return feats


def pci_rsi_plan(
    cells: List[Dict[str, Any]],
    tech_field: str = "rat",
    band_field: str = "freq_band",
    reuse_distance_km: float = 5.0,
    check_mod3: bool = True,
    check_mod6: bool = False,
    check_mod30: bool = True,
    custom_reuse_distance: Optional[Dict[str, float]] = None,
    directional_filter: bool = True,
    rat_filter: Optional[str] = None,
    freq_band_filter: Optional[str] = None,
    locked_pcis: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """
    主入口: RFTools 风格 PCI 分配.

    :param cells: 全部 cells (in-place 改写 new_pci)
    :param tech_field: 制式字段名 (默认 "rat": LTE/NR)
    :param band_field: 频段字段名 (默认 "freq_band")
    :param reuse_distance_km: 默认 PCI 复用距离 (5km)
    :param check_mod3: Mod3 冲突检查 (2 × reuse_distance 内)
    :param check_mod6: Mod6 冲突检查 (RFTools 新增)
    :param check_mod30: Mod30 冲突检查 (NR, 5G DMRS)
    :param custom_reuse_distance: {ecgi: km} 单小区自定义复用距离 (优先级最高)
    :param directional_filter: True=方向背向(双向)的小区对豁免距离冲突
    :return: {cells, log, stats}
    """
    from pci_evaluator import normalize_freq_band
    from pci_scope import cell_freq_band_key, cell_matches_pci_scope, normalize_rat_filter, scope_log_label

    log: List[str] = []
    log.append(f"[RFTools PCI] 输入小区: {len(cells)}")
    if rat_filter or (freq_band_filter and str(freq_band_filter).strip()):
        log.append(f"[RFTools PCI] 规划范围: {scope_log_label(rat_filter, freq_band_filter)}")
    if directional_filter:
        log.append("[RFTools PCI] 方向性过滤: 启用 (背向小区对豁免)")

    if locked_pcis:
        for c in cells:
            ecgi = c.get("ecgi")
            if ecgi and ecgi in locked_pcis:
                c["new_pci"] = int(locked_pcis[ecgi])
                c["locked"] = True

    rat_norm = normalize_rat_filter(rat_filter)
    fb_norm = (
        normalize_freq_band(str(freq_band_filter).strip())
        if freq_band_filter and str(freq_band_filter).strip()
        else None
    )

    # 分组: (rat, freq_band)，并按规划范围过滤
    groups: Dict[Tuple[str, Optional[str]], List[Dict[str, Any]]] = {}
    for c in cells:
        if not cell_matches_pci_scope(c, rat_filter, freq_band_filter):
            continue
        rat_v = c.get(tech_field, "LTE")
        if rat_norm and rat_v != rat_norm:
            continue
        band_v = c.get(band_field) or "默认"
        if fb_norm and cell_freq_band_key(c) != fb_norm:
            continue
        groups.setdefault((rat_v, band_v), []).append(c)

    log.append(f"[RFTools PCI] 分组数: {len(groups)} (按制式+频段)")

    for (rat_v, band_v), feats in groups.items():
        pool = _cell_pool(rat_v)
        pci_min = pool[0]
        pci_max = pool[-1]
        log.append(f"[RFTools PCI] {rat_v}/{band_v}: {len(feats)}小区, pool=[{pci_min}, {pci_max}]")

        # ── 同站多扇区预分配 (SSS 算法, 4G/5G 一致, 保证 N_ID(1) 共享) ──
        from pci_sss_constraints import preassign_same_site_sss
        sss_locked = preassign_same_site_sss(feats, rat_v, reuse_distance_km=reuse_distance_km)
        if sss_locked:
            log.append(f"[RFTools PCI] SSS 预分配 {len(sss_locked)} 小区 (同站 N_ID(1) 共享)")

        # 应用单小区自定义复用距离
        for f in feats:
            ecgi = f.get("ecgi")
            if custom_reuse_distance and ecgi in custom_reuse_distance:
                # SSS 已分配的小区不再二次规划
                if ecgi in sss_locked:
                    continue
                _plan_group(
                    [f], rat_v, pci_min, pci_max,
                    custom_reuse_distance[ecgi],
                    check_mod3, check_mod6, check_mod30,
                    directional_filter,
                )
            else:
                pass  # 默认会在下面的 _plan_group 中处理

        # 组内统一规划 (含自定义)
        # 简单方案: 把 cells 拆分为两类 (有自定义 / 无自定义), 分别规划
        if custom_reuse_distance:
            cust = [f for f in feats if f.get("ecgi") in custom_reuse_distance]
            rest = [f for f in feats if f.get("ecgi") not in custom_reuse_distance]
            if cust:
                # SSS 已分配的 cell 移除 (不再分配)
                cust = [f for f in cust if f["ecgi"] not in sss_locked]
                if cust:
                    for f in cust:
                        f["new_pci"] = None  # 重置
                    _plan_group(cust, rat_v, pci_min, pci_max,
                                custom_reuse_distance[f["ecgi"]],
                                check_mod3, check_mod6, check_mod30,
                                directional_filter)
                    # 把 cust 的 PCI 锁住, 传给 rest
                    for f in cust:
                        f["new_pci"] = f.get("new_pci")
            if rest:
                # SSS 已分配的 cell 移除
                rest = [f for f in rest if f["ecgi"] not in sss_locked]
                if rest:
                    _plan_group(rest, rat_v, pci_min, pci_max,
                                reuse_distance_km,
                                check_mod3, check_mod6, check_mod30,
                                directional_filter)
        else:
            # 清空所有 new_pci (保留 SSS 已分配的)
            for f in feats:
                if f["ecgi"] not in sss_locked:
                    f["new_pci"] = None
            # 剔除 SSS 已分配
            to_plan = [f for f in feats if f["ecgi"] not in sss_locked]
            if to_plan:
                _plan_group(to_plan, rat_v, pci_min, pci_max,
                            reuse_distance_km,
                            check_mod3, check_mod6, check_mod30,
                            directional_filter)

    # 统计
    assigned = sum(1 for c in cells if c.get("new_pci") is not None)
    locked_count = sum(1 for c in cells if _is_locked(c))

    return {
        "cells": cells,
        "log": log,
        "stats": {
            "engine": "rftools",
            "total": len(cells),
            "assigned": assigned,
            "locked": locked_count,
            "groups": len(groups),
        }
    }
