"""
单站/批量规划主流程

封装"单站规划"与"批量规划"两个高层函数,
根据 engine 参数调度 legacy (pci_planner) 或 rftools (pci_rsi_planner)。
"""
from __future__ import annotations

import copy
import math
import random
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from data_parser import parse_work_params
from geo_utils import mutual_back_facing, vincenty_distance
from nbr_planner import plan_neighbors
from pci_planner import plan_all as legacy_plan_all, greedy_allocate, _cell_pool
from pci_scope import cells_same_freq_band_for_pci
from pci_rsi_planner import pci_rsi_plan
from sector_params import enrich_cell_with_sector
from site_type_ext import (
    build_per_site_thresholds,
    get_pci_distance_thresholds,
    to_plan_site_type,
)


# 批量上限
BATCH_MAX_ROWS = 500


def expand_site_to_cells(
    lat: float,
    lon: float,
    rat: str,
    freq_band: str,
    plan_site_type: str,
    n_sectors: int,
    base_azimuth: Union[float, List[float]] = 0.0,
    site_name: Optional[str] = None,
    earfcn: Optional[int] = None,
    tac: Optional[int] = None,
    name_hint: Optional[str] = None,
    ecgi_hint: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    根据扇区数展开为 N 个小区 dict.
    - base_azimuth 为 float: 各扇区按等间距分配 (旧逻辑)
    - base_azimuth 为 list: 第 i 个扇区直接使用 list[i] 作为方位角
    返回的 dict 还未做 sector enrich, 需调用方 enrich
    """
    rat_norm = "NR" if rat in ("NR", "5G", "5G NR", "gNB") else "LTE"
    if rat_norm == "NR":
        site_type_label = {
            "macro": "陆地NR", "micro": "微站NR", "indoor": "室分NR"
        }.get(plan_site_type, "陆地NR")
    else:
        site_type_label = {
            "macro": "陆地LTE", "micro": "微站LTE", "indoor": "室分LTE"
        }.get(plan_site_type, "陆地LTE")

    site_label_zh = {
        "macro": "宏站", "micro": "微站", "indoor": "室分"
    }.get(plan_site_type, "宏站")

    base_name = name_hint or site_name or f"PLAN_{site_label_zh}"
    az_list: List[float]
    if isinstance(base_azimuth, (list, tuple)):
        az_list = list(base_azimuth)
    else:
        az_list = [(float(base_azimuth) + i * 360.0 / n_sectors) % 360.0 for i in range(n_sectors)]

    cells: List[Dict[str, Any]] = []
    for i in range(n_sectors):
        az = az_list[i] % 360.0
        # 生成 ECGI
        if ecgi_hint:
            ecgi = f"{ecgi_hint}-{i+1}" if n_sectors > 1 else ecgi_hint
        else:
            ecgi = f"PLAN-{uuid.uuid4().hex[:8].upper()}-{i+1}"
        # 站点名称
        sname = site_name or f"{base_name}"
        cell = {
            "ecgi": ecgi,
            "name": f"{base_name}_{i+1}" if n_sectors > 1 else base_name,
            "rat": rat_norm,
            "earfcn": earfcn,
            "lon": lon,
            "lat": lat,
            "azimuth": az,
            "radius": None,  # 由 sector_params enrich
            "tac": tac,
            "pci": -1,
            "is_temp": True,  # 标记为临时小区, 不入库
            "site_type": site_label_zh,
            "site_name": sname,
            "site_type_label": site_label_zh,
            "freq_band": freq_band,
            "freq_band_raw": freq_band,
            "plan_freq_band": freq_band,  # 单站页面选择的频段，导出邻区表「源频段」用
            "beamwidth": 65.0,
            "beam": 65.0,
            "n_sectors": n_sectors,
            "base_azimuth": base_azimuth,
            "plan_site_type": plan_site_type,
            "sector_index": i + 1,
            "locked": False,
        }
        enrich_cell_with_sector(cell)
        cells.append(cell)
    return cells


def _build_per_site_thresholds(state_cells: List[Dict[str, Any]]) -> Dict[str, Tuple[float, float]]:
    """
    按 plan_site_type 构建 per-site 阈值, 与全局默认 (1500m, 30000m) 取并集
    """
    return build_per_site_thresholds(state_cells, default_safe_m=1500.0, default_same_pci_min_m=30000.0)


def _pci_scope_key(cell: Dict[str, Any]) -> Tuple[Any, ...]:
    """同制式 + 同规划频段视为 PCI 冲突同一范围（批量多扇区合并用）"""
    fb = cell.get("plan_freq_band") or cell.get("freq_band") or ""
    return (cell.get("rat"), str(fb).strip())


def _dedupe_cells_by_ecgi(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for c in cells:
        e = c.get("ecgi")
        if e and e in seen:
            continue
        if e:
            seen.add(e)
        out.append(c)
    return out


def _build_pci_planning_units(
    new_cells: List[Dict[str, Any]], batch_plan_pci: bool
) -> List[Tuple[Tuple, List[Dict[str, Any]]]]:
    """
    批量规划：同经纬度 + 同制式/频段的多行合并为一个 SSS 站组（即使未填统一基站名）。
    单站/非批量：仍按 group_cells_by_site（site_name 优先）。
    """
    from pci_sss_constraints import group_cells_by_site

    if not batch_plan_pci:
        units: List[Tuple[Tuple, List[Dict[str, Any]]]] = []
        for sk, grp in group_cells_by_site(new_cells).items():
            deduped = _dedupe_cells_by_ecgi(grp)
            if deduped:
                units.append((sk, deduped))
        return units

    from collections import defaultdict

    buckets: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for c in new_cells:
        lat = round(float(c["lat"]), 6)
        lon = round(float(c["lon"]), 6)
        buckets[(lat, lon, _pci_scope_key(c))].append(c)

    units = []
    for bucket_key in sorted(buckets.keys()):
        grp = _dedupe_cells_by_ecgi(buckets[bucket_key])
        if not grp:
            continue
        lat, lon, scope = bucket_key
        phy = (grp[0].get("phy_name") or "").strip()
        if phy:
            site_key: Tuple = ("phy", phy, scope)
        elif len(grp) >= 2:
            site_key = ("batch_coord", lat, lon, scope[0], scope[1])
        else:
            site_key = ("site", grp[0].get("site_name") or f"coord_{lat}_{lon}")
        if len(grp) >= 2:
            unified = phy or f"批量规划站_{lat}_{lon}"
            for c in grp:
                c["site_name"] = unified
        units.append((site_key, grp))
    return units


def _run_pci_planning(
    cells: List[Dict[str, Any]],
    planned_indices: List[int],
    engine: str = "legacy",
    reuse_distance_km: float = 5.0,
    check_mod6: bool = False,
    check_mod30: bool = True,
    target_indices: Optional[List[int]] = None,
    progress_cb: Optional[Any] = None,
    directional_filter: bool = True,
    batch_plan_pci: bool = False,
) -> Dict[str, Any]:
    """
    局部 PCI 规划 (O(target² + target×N))：
    - 新小区从空白分配 PCI
    - 已有小区作为冲突源（仅取 PCI 值，不参与遍历）
    - 不触发全量重算
    directional_filter: 跨站冲突判断时, 双向背向的小区对豁免 (同站物理约束保持)
    """
    log: List[str] = []

    # 已有小区的 PCI 快照（用于冲突检测，不遍历）
    existing_pci: Dict[str, Tuple[int, float, float]] = {}
    # 方向性过滤所需: ecgi -> azimuth / beamwidth 快照
    _existing_azimuth: Dict[str, Optional[float]] = {}
    _existing_beamwidth: Dict[str, Optional[float]] = {}
    for idx, c in enumerate(cells):
        if target_indices and idx in target_indices:
            continue
        lat_v = c.get("lat")
        lon_v = c.get("lon")
        p = c.get("new_pci")
        if p is not None:
            existing_pci[c["ecgi"]] = (int(p), lat_v, lon_v)
        elif c.get("pci") is not None and int(c["pci"]) >= 0:
            existing_pci[c["ecgi"]] = (int(c["pci"]), lat_v, lon_v)
        _existing_azimuth[c["ecgi"]] = c.get("azimuth")
        _existing_beamwidth[c["ecgi"]] = c.get("beamwidth", c.get("beam"))

    new_cells = [cells[i] for i in (target_indices or planned_indices)]
    ecgi_index: Dict[str, Dict[str, Any]] = {c.get("ecgi"): c for c in cells if c.get("ecgi")}
    rat = new_cells[0].get("rat", "LTE") if new_cells else "LTE"
    pool = _cell_pool(rat)
    pool_set = set(pool)
    whitelist: Set[int] = set()
    blacklist: Set[int] = set()
    per_site = _build_per_site_thresholds(new_cells)

    def _dist_km(a: Dict[str, Any], b: Dict[str, Any]) -> float:
        return vincenty_distance(a["lat"], a["lon"], b["lat"], b["lon"]) / 1000.0

    def _thresholds_for(c: Dict[str, Any]) -> Tuple[float, float]:
        ecgi = c["ecgi"]
        if per_site and ecgi in per_site:
            ps_safe, ps_same = per_site[ecgi]
            return (min(ps_safe / 1000.0, reuse_distance_km),
                    min(ps_same / 1000.0, 30.0))
        return (reuse_distance_km, 30.0)

    def _reuse_score(target: Dict[str, Any], pci: int, exclude_ecgi: str = None) -> float:
        """复用距离评分：越小冲突越严重，取 min(实际距离, reuse_km+1)。越大越好。"""
        best = 1e9
        # 已有小区
        for ecgi, (p, other_lat, other_lon) in existing_pci.items():
            if ecgi == exclude_ecgi or int(p) != pci:
                continue
            ex_cell = ecgi_index.get(ecgi)
            if ex_cell and not cells_same_freq_band_for_pci(target, ex_cell):
                continue
            if other_lat is None or other_lon is None:
                continue
            d = vincenty_distance(target["lat"], target["lon"], other_lat, other_lon) / 1000.0
            if d < best:
                best = d
        # 同批新建小区（排除自身）
        for c in new_cells:
            if c["ecgi"] == exclude_ecgi:
                continue
            p = c.get("new_pci")
            if p is None or int(p) != pci:
                continue
            try:
                d = _dist_km(target, c)
            except Exception:
                continue
            if d < best:
                best = d
        # cap：超过 reuse_km 视为"无冲突"，score = reuse_km + 1
        return min(best, reuse_distance_km + 1)

    # 清理待分配小区已有 PCI
    for c in new_cells:
        c.pop("new_pci", None)
        c.pop("pci_candidates", None)
        c.pop("pci_groups", None)

    # ── 站组：批量时同经纬度+同频段合并（未填统一基站名也能 mod3 分扇区）；单站仍按 site_name ──
    from collections import defaultdict

    planning_units = _build_pci_planning_units(new_cells, batch_plan_pci=batch_plan_pci)
    if batch_plan_pci and planning_units:
        merged = sum(len(g) for _, g in planning_units)
        log.append(f"[PCI] 批量站组 {len(planning_units)} 组 / {merged} 扇区（同坐标同频段已合并）")
    coord_to_site_keys: Dict[Tuple[float, float], List[Tuple]] = defaultdict(list)
    for sk, grp in planning_units:
        if not grp:
            continue
        ck = (round(float(grp[0]["lat"]), 6), round(float(grp[0]["lon"]), 6))
        coord_to_site_keys[ck].append(sk)

    # ── 同站已规划小区：本批次 new_cells 之外的、与 new_cells 同 site_name ──
    # 这些小区可能是之前几次单站规划累积下来的 "PLAN_宏站" 兄弟扇区,它们必须共享 nid1
    # (硬性 SSS 校验按 site_name 分桶),因此本次规划必须强制沿用它们的 nid1 + 已用 mod3 槽位
    same_site_existing: Dict[Tuple[float, float], List[Tuple[int, int]]] = defaultdict(list)
    # 记录"同 site_name"的 ecgi,后续用于同 site_name 桶的 nid1 强制沿用
    same_site_name_existing: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    # 收集本批次所有目标站名
    new_site_names: set = {c.get("site_name") for c in new_cells if c.get("site_name")}
    for ecgi_ex, (p, olat, olon) in existing_pci.items():
        if p is None or int(p) < 0:
            continue
        nid1_val = int(p) // 3
        mod3_val = int(p) % 3
        ex_cell = ecgi_index.get(ecgi_ex)
        ex_site_name = ex_cell.get("site_name") if ex_cell else None
        # 同 site_name 命中: 记录到 site_name 维度,后续按 site_name 桶强制沿用
        if ex_site_name and ex_site_name in new_site_names:
            same_site_name_existing[ex_site_name].append((nid1_val, mod3_val))
            # 同时记录到 coord 维度,方便 site_groups 桶对齐
            if olat is not None and olon is not None:
                coord_key = (round(olat, 6), round(olon, 6))
                for sk in coord_to_site_keys.get(coord_key, []):
                    same_site_existing[sk].append((nid1_val, mod3_val))
            continue
        # 同坐标 (兜底: 仅当 ex_cell 没有 site_name 时才视为同站,否则属不同站点,绝不共享 nid1)
        # ── 修复:之前错误地按"同坐标"硬塞,会把 site_name 不同的相邻小区 (例如真实工参站 vs
        #     本次规划的"PLAN_宏站") 视为同站,导致新站被迫沿用老 nid1 + 老 mod3 槽位,
        #     3 扇区时只有 2 个可用 mod3,出现 PCI 重复触发 SSS 校验失败 ──
        if ex_site_name:
            continue
        if olat is None or olon is None:
            continue
        coord_key = (round(olat, 6), round(olon, 6))
        for sk in coord_to_site_keys.get(coord_key, []):
            same_site_existing[sk].append((nid1_val, mod3_val))

    for site_key, s_cells in planning_units:
        if not s_cells:
            continue
        n = len(s_cells)
        site_lat = s_cells[0]["lat"]
        site_lon = s_cells[0]["lon"]
        coord_key = (round(site_lat, 6), round(site_lon, 6))
        # ── 收集 nearby pci: 半径 = max(reuse, same_pci_min) 以覆盖同PCI冲突 ──
        # 同站 SSS 算法走 PciEvaluator.score_group, 远距邻居自动 smoothstep=0 无影响
        from pci_evaluator import PciEvaluator
        _eval_for_radius = PciEvaluator.from_cell(
            s_cells[0], check_mod30=check_mod30, directional_filter=directional_filter
        )
        nearby_radius = max(reuse_distance_km, _eval_for_radius.same_pci_min_km)
        nearby_pcis: List[Tuple[int, float]] = []
        ref_cell = s_cells[0]
        for ecgi2, (p, olat, olon) in existing_pci.items():
            if olat is None or olon is None or p is None or p < 0:
                continue
            ex_cell = ecgi_index.get(ecgi2)
            if ex_cell and not cells_same_freq_band_for_pci(ref_cell, ex_cell):
                continue
            try:
                d = vincenty_distance(site_lat, site_lon, olat, olon) / 1000.0
            except Exception:
                continue
            if d <= nearby_radius:
                nearby_pcis.append((int(p), d))

        # ── 同站 SSS / N_ID(1) 共享 + mod3 隔离 (4G LTE 与 5G NR 一致) ──
        # PCI = 3 × N_ID(1) + N_ID(2)
        # - 4G: PCI ∈ [0, 503]  → N_ID(1) ∈ [0, 167]
        # - 5G: PCI ∈ [0, 1007] → N_ID(1) ∈ [0, 335]
        # 同站所有扇区共享同一 N_ID(1), 各扇区 mod3=0,1/2 (N_ID(2))

        # ── 整站挑一个 nid1（同站 SSS 必须共享），各扇区 mod3=0,1,2 ──
        # ── 频段自适应阈值: 从 group 中第一个 cell 取 (plan_site_type, freq_band) ──
        from pci_evaluator import PciEvaluator, pick_best_nid1
        _evaluator = PciEvaluator.from_cell(
            s_cells[0], check_mod30=check_mod30, directional_filter=directional_filter
        )
        safe_km_now = _evaluator.safe_dist_km
        same_pci_min_now = _evaluator.same_pci_min_km
        n_sectors = len(s_cells)

        def _pci_group_score(nid1_try: int) -> float:
            """整组 (N 个 PCI) 的冲突评分 — 走 PciEvaluator 统一评估 (mod3/mod30/同PCI).
            nearby_pcis 是 (pci, dist_km) 二元组, 走 score_group 的 neighbors_simple 路径.
            """
            target_pcis = [nid1_try * 3 + (j % 3) for j in range(n_sectors)]
            return _evaluator.score_group(target_pcis, s_cells,
                                         neighbors=None, neighbors_simple=nearby_pcis)

        # ── 根据 rat 确定 nid1 搜索范围 ──
        # 4G: N_ID(1) 上限 = 167 (PCI 503 / 3); 5G: N_ID(1) 上限 = 335 (PCI 1007 / 3)
        nid1_max = 167 if rat == "LTE" else 335

        # ── 复用同站已有的 nid1：若之前已规划过同站小区 (按 site_name 或坐标),
        #     必须沿用, 否则 SSS 硬性校验会失败 ──
        site_existing = list(same_site_existing.get(site_key, []))
        new_site_name = s_cells[0].get("site_name")
        if new_site_name and new_site_name in same_site_name_existing:
            site_existing.extend(same_site_name_existing[new_site_name])

        if site_existing:
            # 取首个已用 nid1 作为强约束 (同站所有扇区共享)
            site_nid1 = site_existing[0][0]
            existing_used_mod3 = {m for _, m in site_existing}
            # mod3 槽位: 优先分配尚未被同站老扇区占用的, 再回到 0/1/2 顺序
            available_mods = [m for m in (0, 1, 2) if m not in existing_used_mod3]
            if not available_mods and n_sectors <= 3 - len(existing_used_mod3):
                # 理论上: 同站已有 N 个 mod3,本次 n_sectors=N 时正好占完剩余槽位
                # 但 available_mods 为空说明 N == len(existing_used_mod3), 不会有剩余
                # 此分支保留为防御性, 实际不会进入
                pass
            elif not available_mods:
                # 同站已有 3 个扇区占用 0/1/2 全部 mod3,本次仍要规划
                # (main.py 入口已 _purge_planned_temp_cells 清掉临时小区, 此路径主要兜底非临时同站扇区)
                # 安全兜底: 让新扇区沿用 0/1/2 顺序 (即使 PCI 与老扇区相同, 至少 nid1 一致不触发 SSS 失败)
                available_mods = [0, 1, 2]
            # 把 available_mods 顺序补齐到 n_sectors (超 3 扇区时按 i%len(available_mods) 循环)
            mod_assignment = [available_mods[i % len(available_mods)] for i in range(n_sectors)]
        else:
            # 无同站历史: 算法挑最佳 nid1 (走 PciEvaluator)
            # 把 (pci, dist_km) 转 (pci, cell_dict, dist_km) 给 evaluator
            site_lat_avg = sum(c.get("lat", 0) for c in s_cells) / n_sectors
            site_lon_avg = sum(c.get("lon", 0) for c in s_cells) / n_sectors
            neighbors_for_eval: List[Tuple[int, Dict[str, Any], float]] = []
            for nbr_pci, d_km in nearby_pcis:
                # 占位 cell: 用 site center, 不影响 distance 评分; 方向判定用 site 内各扇区方位
                neighbors_for_eval.append((int(nbr_pci), {
                    "lat": site_lat_avg, "lon": site_lon_avg,
                    "azimuth": 0, "beamwidth": 360,
                }, float(d_km)))
            best_nid1 = pick_best_nid1(nid1_max, n_sectors, neighbors_for_eval,
                                       s_cells, _evaluator)
            site_nid1 = best_nid1
            mod_assignment = [i % 3 for i in range(n_sectors)]
        for i, c in enumerate(s_cells):
            target_mod = mod_assignment[i]
            c["_nid1"] = site_nid1
            c["_nid2"] = target_mod
            c["_forced_pci"] = site_nid1 * 3 + target_mod

        # 批量迭代：本组 PCI 立即进入全局冲突池，后续站组规划时不能再分配相同 PCI
        if batch_plan_pci:
            for c in s_cells:
                fp = c.get("_forced_pci")
                if fp is None:
                    continue
                pci_int = int(fp)
                c["new_pci"] = pci_int
                existing_pci[c["ecgi"]] = (pci_int, c.get("lat"), c.get("lon"))

        nid1_vals = [c["_nid1"] for c in s_cells]
        log.append(f"[DEBUG] site nearby={len(nearby_pcis)} site_nid1={site_nid1} per-sector nid1={nid1_vals}")

        # ── 按 SSS 组生成候选: 当前组 + 4 个高分候选组 ──
        # 评分口径与选择算法一致, 复用上面的 _pci_group_score
        _group_score = _pci_group_score

        # 当前组
        current_group_pcis = [site_nid1 * 3 + (j % 3) for j in range(n_sectors)]
        current_group_score = _group_score(site_nid1)

        # 候选组: 排除当前 nid1, 取 N 个 PCI 为一组的 top-4
        candidate_groups: List[Tuple[float, int, List[int]]] = []
        for nid1_try in range(nid1_max + 1):
            if nid1_try == site_nid1:
                continue
            pcis = [nid1_try * 3 + (j % 3) for j in range(n_sectors)]
            score = _group_score(nid1_try)
            candidate_groups.append((score, nid1_try, pcis))
        candidate_groups.sort(key=lambda x: -x[0])
        alt_groups = candidate_groups[:4]

        pci_groups = [
            {
                "sss_group": int(site_nid1),
                "pcis": [int(p) for p in current_group_pcis],
                "score": round(current_group_score, 3),
                "is_current": True,
            }
        ]
        for s, nid1_alt, pcis_alt in alt_groups:
            pci_groups.append({
                "sss_group": int(nid1_alt),
                "pcis": [int(p) for p in pcis_alt],
                "score": round(s, 3),
                "is_current": False,
            })

        # 写入本站所有扇区
        for c in s_cells:
            c["pci_groups"] = pci_groups

    # 清掉冗余 DEBUG
    while log and 'per-sector nid1=' in log[-1]:
        log.pop()

    total_new = max(1, len(new_cells))
    for i, c in enumerate(new_cells):
        nid1 = c.get("_nid1")
        nid2 = c.get("_nid2")
        forced_pci = c.get("_forced_pci")

        # ── SSS 约束最终生效：同站 PCI 直接锁定 ──
        if forced_pci is not None:
            site_nid1 = c.get("_nid1")
            # ── 同站 SSS 组锁定 + mod3 隔离 ──
            # 5G NR: PCI = 3×N_ID(1) + N_ID(2)，同站必须 N_ID(1) 相同、N_ID(2)=0/1/2 各一个
            # 候选 PCI 排除本扇区已选 new_pci (避免显示 "候选里包含结果" 的自相矛盾)
            # 同时显示 2 个同组备选 + 跨组高分备选, 让用户看到"换 SSS 组"的可能性
            target_mod = c.get("_nid2")
            forced_int = int(forced_pci)
            c["new_pci"] = forced_int
            c["pci_candidates_primary"] = forced_int

            # ── 频段自适应评估器 (跨站 mod3/mod30/同PCI 三档) ──
            from pci_evaluator import PciEvaluator
            _eval_sss = PciEvaluator.from_cell(
                c, check_mod30=check_mod30, directional_filter=directional_filter
            )

            # ── 构造邻居列表 (existing_pci + new_cells 互为邻居) ──
            eval_neighbors: List[Tuple[int, Dict[str, Any], float]] = []
            for ecgi_ex, (p, olat, olon) in existing_pci.items():
                if p is None or int(p) < 0 or olat is None or olon is None:
                    continue
                ex_cell = ecgi_index.get(ecgi_ex)
                if ex_cell and not cells_same_freq_band_for_pci(c, ex_cell):
                    continue
                try:
                    d = vincenty_distance(c["lat"], c["lon"], olat, olon) / 1000.0
                except Exception:
                    continue
                if d > max(_eval_sss.safe_dist_km * 2, _eval_sss.same_pci_min_km):
                    continue
                eval_neighbors.append((int(p), {
                    "lat": olat, "lon": olon,
                    "azimuth": _existing_azimuth.get(ecgi_ex),
                    "beamwidth": _existing_beamwidth.get(ecgi_ex),
                }, d))
            for other in new_cells:
                if other["ecgi"] == c["ecgi"]:
                    continue
                op = other.get("new_pci")
                if op is None:
                    continue
                try:
                    d = vincenty_distance(c["lat"], c["lon"],
                                          float(other["lat"]), float(other["lon"])) / 1000.0
                except Exception:
                    continue
                if d > max(_eval_sss.safe_dist_km * 2, _eval_sss.same_pci_min_km):
                    continue
                eval_neighbors.append((int(op), other, d))

            # ── 硬冲突检测: 选定 PCI 与跨站邻居的 mod3/mod30/同PCI 距离冲突 ──
            violations = _eval_sss.hard_violations(forced_int, c, eval_neighbors)
            c["pci_violations"] = violations  # 给前端/导出参考
            if violations:
                log.append(f"[WARN] {c['ecgi']} PCI={forced_int} SSS锁定但存在跨站硬冲突: {violations}")

            # ── 评分函数: 改用 PciEvaluator 统一公式 (mod3+mod30+同PCI) ──
            def _eval_pci_score(pci: int) -> float:
                return _eval_sss.score_cell(int(pci), c, eval_neighbors)

            # ── 本 SSS 组内候选: 3 个 PCI, 按 mod3 排列 ──
            same_group = [(site_nid1 * 3 + nid2, site_nid1) for nid2 in range(3)]
            same_group.sort(key=lambda x: -(x[0] == forced_int))  # 强制首选排第 1 位 (信息保留)

            # ── 跨组候选: 取 N_ID(2) 与本扇区相同的 PCI (mod3 相同但 SSS 不同) ──
            cross_group = []
            for other_nid1 in range(0, 1008):  # N_ID(1) 范围 0..1007
                if other_nid1 == site_nid1:
                    continue
                pp = other_nid1 * 3 + target_mod
                s = _eval_pci_score(pp)
                cross_group.append((s, pp, other_nid1))
            cross_group.sort(key=lambda x: x[0])  # 越小越好
            cross_top3 = cross_group[:3]

            # ── 拼装候选: 先同组 (排除本扇区 new_pci) ──
            cands = []
            for pp, g in same_group:
                if pp == forced_int:
                    continue  # 排除本扇区已选
                s = _eval_pci_score(pp)
                cands.append({"pci": int(pp), "score": round(s, 3), "sss_group": int(g),
                              "scope": "同组"})

            # 再追加跨组高分 PCI (供用户考虑换 SSS 组)
            for s, pp, g in cross_top3:
                cands.append({"pci": int(pp), "score": round(s, 3), "sss_group": int(g),
                              "scope": "跨组"})

            c["pci_candidates"] = cands
            log.append(f"[DEBUG] {c['ecgi']} SSS nid1={site_nid1} forced_pci={forced_int} (mod{target_mod}) "
                       f"candidates={[(c['pci'],c['scope']) for c in cands]}")
            continue

        # ── 非同站场景：走原 forbidden 流程 ──
        cell_safe_km, same_pci_min_km = _thresholds_for(c)
        forbidden: Set[int] = set()

        for ecgi, (p, other_lat, other_lon) in existing_pci.items():
            if other_lat is None or other_lon is None:
                continue
            ex_cell = ecgi_index.get(ecgi)
            if ex_cell and not cells_same_freq_band_for_pci(c, ex_cell):
                continue
            d_km = vincenty_distance(c["lat"], c["lon"], other_lat, other_lon) / 1000.0
            if d_km >= cell_safe_km and d_km >= 3.0 and d_km >= same_pci_min_km:
                continue
            # 方向性豁免: 双向背向则跳过 (跨站才有意义; 同站 cell_safe_km 通常 < safe 距离)
            if directional_filter and d_km >= cell_safe_km:
                other_cell = ex_cell if ex_cell else {
                    "lat": other_lat, "lon": other_lon,
                    "azimuth": _existing_azimuth.get(ecgi),
                    "beamwidth": _existing_beamwidth.get(ecgi),
                }
                if other_cell["azimuth"] is not None and mutual_back_facing(c, other_cell):
                    continue
            if d_km < cell_safe_km:
                forbidden.update({pp for pp in pool_set if pp % 3 == p % 3})
                if rat == "NR" and check_mod30:
                    forbidden.update({pp for pp in pool_set if pp % 30 == p % 30})
            elif d_km < 3.0:
                forbidden.update({pp for pp in pool_set if pp % 3 == p % 3})
                if rat == "NR" and check_mod30:
                    forbidden.update({pp for pp in pool_set if pp % 30 == p % 30})
            elif d_km < same_pci_min_km:
                forbidden.add(p)

        for other in new_cells:
            if other["ecgi"] == c["ecgi"]:
                continue
            p = other.get("new_pci")
            if p is None:
                continue
            try:
                d_km = _dist_km(c, other)
            except Exception:
                continue
            # 方向性豁免: 同站 (cell_safe_km 内) 不豁免, 物理 mod3 必须
            if directional_filter and d_km >= cell_safe_km and mutual_back_facing(c, other):
                continue
            if d_km < cell_safe_km:
                forbidden.update({pp for pp in pool_set if pp % 3 == p % 3})
                if rat == "NR" and check_mod30:
                    forbidden.update({pp for pp in pool_set if pp % 30 == p % 30})
            elif d_km < 3.0:
                forbidden.update({pp for pp in pool_set if pp % 3 == p % 3})
            elif d_km < same_pci_min_km:
                forbidden.add(int(p))

        candidates = [pp for pp in pool_set if pp not in forbidden]
        if not candidates:
            candidates = list(pool_set)

        scored = [(_reuse_score(c, pp), pp) for pp in candidates]
        scored.sort(key=lambda x: -x[0])
        top_n = min(50, len(scored))
        top_candidates = scored[:top_n]
        best = random.choice([pp for _, pp in top_candidates])
        c["new_pci"] = int(best)
        c["pci_candidates"] = [{"pci": int(pp), "score": round(s, 3)} for s, pp in top_candidates]

        # 进度回调 (PCI 阶段: 30%~70%)
        if progress_cb:
            try:
                pct = 30 + int((i + 1) / total_new * 40)
                progress_cb(min(70, pct), f"PCI 分配 {i + 1}/{total_new}")
            except Exception:
                pass

    log.append(f"[PCI] 局部规划完成: {len(new_cells)} 个 {rat} 小区")
    for nc in new_cells:
        nc.pop("_nid1", None)
        nc.pop("_nid2", None)
        nc.pop("_forced_pci", None)
        log.append(f"  {nc['ecgi']} → PCI {nc['new_pci']}")

    # ── 硬性校验: 同站 N_ID(1) 共享 + mod3 分布 (4G/5G 通用) ──
    from pci_sss_constraints import assert_same_site_sss_shared
    assert_same_site_sss_shared(cells, pci_field="new_pci", group_by="auto")

    return {"cells": cells, "log": log, "stats": {"engine": engine, "local_mode": True}}


def _run_neighbor_planning(
    cells: List[Dict[str, Any]],
    nbr_plan_types: Optional[List[str]] = None,
    use_beam_overlap_score: bool = False,
    # ── 局部邻区: 仅对指定 ECGI 列表内的源小区规划邻区 ──
    target_ecgis: Optional[List[str]] = None,
    # ── 局部邻区: 仅规划距离 target_ecgis 在此半径内的候选邻区 ──
    max_distance_km: float = 10.0,
    # ── 邻区得分阈值: 低于此得分的候选邻区直接丢弃 ──
    # (实际工程中低于 0.5 的邻区关系不必要, 会导致冗余邻区过多)
    score_threshold: float = 0.5,
    # ── 第一圈邻区半径: 距离 ≤ 此值的小区强制加入 (不因 score 过滤) ──
    # 工程惯例: reuse 半径内所有小区都应作为邻区候选, 不论扇区是否正交
    first_ring_km: Optional[float] = None,
    per_src_score_threshold: Optional[Dict[str, float]] = None,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """对全部 cells 执行邻区规划, 可选邻区类型过滤 / 局部过滤"""
    if progress_cb:
        try:
            progress_cb(70, "邻区规划中…")
        except Exception:
            pass
    res = plan_neighbors(
        cells,
        nbr_plan_types=nbr_plan_types,
        use_beam_overlap_score=use_beam_overlap_score,
        target_ecgis=target_ecgis,
        max_distance_km=max_distance_km,
        score_threshold=score_threshold,
        first_ring_km=first_ring_km,
        per_src_score_threshold=per_src_score_threshold,
    )
    if progress_cb:
        try:
            progress_cb(95, "邻区规划完成")
        except Exception:
            pass
    return res


def _group_neighbors_by_type(cells: List[Dict[str, Any]], planned_ecgis: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    把规划小区的邻区按 nbr_type 分组
    返回 {nbr_type: [neighbor_record, ...]}
    """
    by_type: Dict[str, List[Dict[str, Any]]] = {
        "4G_4G": [], "4G_5G": [], "5G_4G": [], "5G_5G": []
    }
    for c in cells:
        if c.get("ecgi") not in planned_ecgis:
            continue
        for n in c.get("neighbors", []):
            t = n.get("nbr_type", "4G_4G")
            by_type.setdefault(t, []).append({
                "src_ecgi": c["ecgi"],
                "src_name": c.get("name"),
                "src_pci": c.get("new_pci", c.get("pci")),
                "src_rat": c.get("rat"),
                "dst_ecgi": n["dst_ecgi"],
                "dst_name": n["dst_name"],
                "distance_m": n["distance_m"],
                "overlap_m2": n["overlap_m2"],
                "score": n["score"],
                "same_freq": n.get("same_freq", False),
                "cross_system": n.get("cross_system", False),
                "auto_added": n.get("auto_added", False),
            })
    return by_type


def plan_single_site(
    state_cells: List[Dict[str, Any]],
    lat: float,
    lon: float,
    rat: str,
    freq_band: str,
    plan_site_type: str,
    n_sectors: int = 3,
    base_azimuth: Union[float, List[float]] = 0.0,
    name_hint: Optional[str] = None,
    site_name: Optional[str] = None,
    earfcn: Optional[int] = None,
    tac: Optional[int] = None,
    nbr_plan_types: Optional[List[str]] = None,
    engine: str = "legacy",
    reuse_distance_km: float = 5.0,
    check_mod6: bool = False,
    check_mod30: bool = True,
    use_beam_overlap_score: bool = False,
    # ── 邻区得分阈值: 默认 0.5, 低于此得分的候选邻区直接丢弃 (避免冗余邻区) ──
    score_threshold: float = 0.5,
    planning_mode: str = "pci+nbr",
    progress_cb: Optional[Any] = None,
    directional_filter: bool = True,
) -> Dict[str, Any]:
    """
    单站规划主流程 (局部模式).

    1. expand_site_to_cells 生成 N 个虚拟 cell
    2. 仅在本地副本中追加 (不污染调用方的 state_cells)
    3. 按 planning_mode 选择性执行: PCI + 邻区
    4. 返回 {center, planned_cells, planned_ecgis, nbr_by_type, log}

    局部模式避免全网 O(N²) 重算，在 19K+ 小区场景下从分钟级降至秒级。
    """
    log: List[str] = []
    log.append(f"[单站规划] lat={lat}, lon={lon}, rat={rat}, freq={freq_band}, type={plan_site_type}")
    log.append(f"[单站规划] 扇区数={n_sectors}, 基方位角={base_azimuth}, engine={engine}")

    if progress_cb:
        try: progress_cb(5, "正在展开扇区…")
        except Exception: pass

    plan_site_type = to_plan_site_type(plan_site_type)
    new_cells = expand_site_to_cells(
        lat=lat, lon=lon, rat=rat, freq_band=freq_band,
        plan_site_type=plan_site_type, n_sectors=n_sectors,
        base_azimuth=base_azimuth,
        site_name=site_name, earfcn=earfcn, tac=tac,
        name_hint=name_hint,
    )
    log.append(f"[DEBUG] new_cells[0] lat={new_cells[0]['lat']} lon={new_cells[0]['lon']}")
    log.append(f"[单站规划] 展开为 {len(new_cells)} 个小区")

    # ── 用本地副本避免污染调用方的 state_cells (即 STATE.cells) ──
    # 否则多次规划会累积 PLAN-* 临时小区, 最终导致 plan_all / check_conflict
    # 把临时小区也一起计算 (且 db_save_all 每次都要过滤它们, 有性能开销)
    work_cells = copy.deepcopy(state_cells) + new_cells
    planned_indices = list(range(len(state_cells), len(state_cells) + len(new_cells)))
    planned_ecgis = [c["ecgi"] for c in new_cells]

    # ── 局部 PCI 规划: 仅对新小区分配 PCI，其余小区作为 locked ──
    if planning_mode in ("pci", "pci+nbr"):
        if progress_cb:
            try: progress_cb(15, "开始 PCI 规划…")
            except Exception: pass
        log.append(f"[单站规划] 局部PCI规划 (reuse={reuse_distance_km}km) ...")
        pci_result = _run_pci_planning(
            work_cells, planned_indices,
            engine=engine,
            reuse_distance_km=reuse_distance_km,
            check_mod6=check_mod6,
            check_mod30=check_mod30,
            target_indices=planned_indices,
            progress_cb=progress_cb,
            directional_filter=directional_filter,
        )
        log.extend(pci_result.get("log", []))
    else:
        pci_result = {"stats": {}, "log": []}
        log.append("[单站规划] 跳过PCI规划 (模式: 仅邻区规划)")

    # ── 局部邻区规划: 仅对新小区做邻区规划 ──
    # first_ring_km = reuse_distance_km: 第一圈 (复用半径) 内所有小区强制作为邻区 (不因 score 过滤)
    # max_distance_km = reuse_distance_km * 1.5: 让"第一圈外"也有候选, 受 score_threshold 控制
    # 这样调整 score_threshold 时, 5km~7.5km 范围的小区数量会变化, 用户能看到阈值效果
    if planning_mode in ("nbr", "pci+nbr"):
        nbr_max_km = reuse_distance_km * 1.5
        log.append(f"[单站规划] 局部邻区规划 (第一圈={reuse_distance_km}km 强制, max={nbr_max_km:.1f}km, score≥{score_threshold}) ...")
        nbr_result = _run_neighbor_planning(
            work_cells,
            nbr_plan_types=nbr_plan_types,
            use_beam_overlap_score=use_beam_overlap_score,
            target_ecgis=planned_ecgis,
            max_distance_km=nbr_max_km,
            score_threshold=score_threshold,
            first_ring_km=reuse_distance_km,
            progress_cb=progress_cb,
        )
        log.extend(nbr_result.get("log", []))
    else:
        nbr_result = {"stats": {}, "log": []}
        log.append("[单站规划] 跳过邻区规划 (模式: 仅PCI规划)")
        for c in work_cells:
            if c.get("ecgi") in planned_ecgis:
                c["neighbors"] = []

    nbr_by_type = _group_neighbors_by_type(work_cells, planned_ecgis) if planning_mode != "pci" else {k: [] for k in ("4G_4G", "4G_5G", "5G_4G", "5G_5G")}
    planned_cells_view = [c for c in work_cells if c["ecgi"] in planned_ecgis]
    center = {
        "lat": lat, "lon": lon,
        "name": planned_cells_view[0].get("name") if planned_cells_view else name_hint,
        "rat": rat, "freq_band": freq_band, "plan_site_type": plan_site_type,
    }

    log.append(f"[单站规划] 完成: {len(planned_cells_view)}扇区, 邻区 {sum(len(v) for v in nbr_by_type.values())}条")

    return {
        "center": center,
        "planned_cells": planned_cells_view,
        "planned_ecgis": planned_ecgis,
        "nbr_by_type": nbr_by_type,
        "nbr_counts": {t: len(v) for t, v in nbr_by_type.items()},
        "log": log,
        "engine": engine,
        "pci_stats": pci_result.get("stats", {}),
        "nbr_stats": nbr_result.get("stats", {}),
    }


def plan_batch_sites(
    state_cells: List[Dict[str, Any]],
    file_bytes: bytes,
    filename: str,
    nbr_plan_types: Optional[List[str]] = None,
    engine: str = "legacy",
    reuse_distance_km: float = 5.0,
    check_mod6: bool = False,
    check_mod30: bool = True,
    use_beam_overlap_score: bool = False,
    planning_mode: str = "pci+nbr",
    progress_cb: Optional[Any] = None,
    directional_filter: bool = True,
) -> Dict[str, Any]:
    """
    批量规划主流程（内存态，不写 STATE / 不入库）.

    1. 解析 xlsx (复用 data_parser)
    2. 与 state_cells（现网工参）在副本上联合 PCI + 邻区规划
    3. 返回规划结果与 export_cells 快照，供导出 xlsx
    """
    log: List[str] = []
    log.append(f"[批量规划] 文件: {filename}, engine={engine}, mode={planning_mode}")

    if progress_cb:
        try: progress_cb(5, "正在解析文件…")
        except Exception: pass

    parsed = parse_work_params(file_bytes, filename)
    if "error" in parsed and not parsed.get("valid_cells"):
        return {
            "success": False,
            "error": parsed.get("error", "解析失败"),
            "log": log,
        }

    valid_cells = parsed.get("valid_cells", [])
    total_parsed = len(valid_cells)
    if total_parsed > BATCH_MAX_ROWS:
        log.append(f"[批量规划] 超过上限 {BATCH_MAX_ROWS}, 截断为 {BATCH_MAX_ROWS} 条 (原始 {total_parsed})")
        valid_cells = valid_cells[:BATCH_MAX_ROWS]
    else:
        log.append(f"[批量规划] 解析有效小区: {total_parsed}")

    if not valid_cells:
        return {
            "success": False,
            "error": "无有效小区",
            "log": log,
            "invalid_rows": parsed.get("invalid_rows", []),
        }

    if progress_cb:
        try: progress_cb(15, f"已解析 {total_parsed} 行")
        except Exception: pass

    # ── 每行模板 = 1 个待规划小区；在内存副本上与现网工参联合规划 ──
    batch_cells: List[Dict[str, Any]] = []
    for c in valid_cells:
        row = copy.deepcopy(c)
        if not row.get("plan_site_type"):
            row["plan_site_type"] = to_plan_site_type(row.get("site_type"))
        row.setdefault("n_sectors", 1)
        row.setdefault("base_azimuth", row.get("azimuth", 0))
        row.setdefault("locked", False)
        row["is_temp"] = True
        row["is_batch_plan"] = True
        if row.get("plan_freq_band") is None or str(row.get("plan_freq_band")).strip() == "":
            raw = row.get("freq_band_raw")
            if raw is not None and str(raw).strip():
                row["plan_freq_band"] = str(raw).strip()
            else:
                row["plan_freq_band"] = row.get("freq_band") or ""
        enrich_cell_with_sector(row)
        batch_cells.append(row)

    all_planned_ecgis = [str(c.get("ecgi")) for c in batch_cells if c.get("ecgi")]
    work_cells = copy.deepcopy(state_cells) + batch_cells
    all_planned_indices = list(range(len(state_cells), len(work_cells)))
    log.append(
        f"[批量规划] 现网 {len(state_cells)} 小区 + 待规划 {len(batch_cells)} 小区（仅内存，不写库）"
    )

    # ── 局部 PCI 规划: 仅对待规划行分配 PCI ──
    if planning_mode in ("pci", "pci+nbr"):
        log.append(f"[批量规划] 局部PCI规划 (reuse={reuse_distance_km}km) ...")
        pci_result = _run_pci_planning(
            work_cells, all_planned_indices,
            engine=engine,
            reuse_distance_km=reuse_distance_km,
            check_mod6=check_mod6,
            check_mod30=check_mod30,
            target_indices=all_planned_indices,
            progress_cb=progress_cb,
            directional_filter=directional_filter,
            batch_plan_pci=True,
        )
        log.extend(pci_result.get("log", []))
    else:
        pci_result = {"stats": {}, "log": []}
        log.append("[批量规划] 跳过PCI规划 (模式: 仅邻区规划)")

    # ── 局部邻区规划: 仅对新增小区做邻区规划 ──
    if planning_mode in ("nbr", "pci+nbr"):
        log.append(f"[批量规划] 局部邻区规划 (reuse={reuse_distance_km}km) ...")
        nbr_result = _run_neighbor_planning(
            work_cells,
            nbr_plan_types=nbr_plan_types,
            use_beam_overlap_score=use_beam_overlap_score,
            target_ecgis=all_planned_ecgis,
            max_distance_km=reuse_distance_km,
            progress_cb=progress_cb,
        )
        log.extend(nbr_result.get("log", []))
    else:
        nbr_result = {"stats": {}, "log": []}
        log.append("[批量规划] 跳过邻区规划 (模式: 仅PCI规划)")

    planned_set = set(all_planned_ecgis)
    if planning_mode == "pci":
        for c in work_cells:
            if c.get("ecgi") in planned_set:
                c["neighbors"] = []
        nbr_by_type = {k: [] for k in ("4G_4G", "4G_5G", "5G_4G", "5G_5G")}
    else:
        nbr_by_type = _group_neighbors_by_type(work_cells, all_planned_ecgis)
    planned_cells_view = [c for c in work_cells if c.get("ecgi") in planned_set]

    return {
        "success": True,
        "planned_cells": planned_cells_view,
        "planned_ecgis": all_planned_ecgis,
        "export_cells": work_cells,
        "nbr_by_type": nbr_by_type,
        "log": log,
        "engine": engine,
        "stats": {
            "total_parsed": total_parsed,
            "planned": len(planned_cells_view),
            "truncated": total_parsed > BATCH_MAX_ROWS,
            "invalid_rows": len(parsed.get("invalid_rows", [])),
            "network_cells": len(state_cells),
        },
        "invalid_rows": parsed.get("invalid_rows", []),
        "pci_stats": pci_result.get("stats", {}),
        "nbr_stats": nbr_result.get("stats", {}),
    }


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """用于聚焦渲染的粗略距离估算"""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
