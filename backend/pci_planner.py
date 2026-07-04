"""
PCI三级规划引擎

第一级 全网粗规划: 地理分区 + 独立资源池 + 贪心分配,最大化同PCI复用距离
第二级 局部微调: 仅重算框选小区及周边辐射范围,保留存量合规小区
第三级 全局二次校验: 全量遍历冲突,自动修复轻度冲突,输出冲突清单
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from conflict_check import check_pair, collect_conflicts  # noqa: I001
from geo_utils import mutual_back_facing, vincenty_distance  # noqa: I001
from pci_evaluator import normalize_freq_band  # noqa: I001
from cell_filters import is_nb_znh_cell
from pci_scope import (  # noqa: I001
    cell_freq_band_key,
    cell_matches_pci_scope,
    clear_new_pci_in_scope,
    filter_cells_pci_scope,
    filter_ecgis_pci_scope,
    lock_out_of_scope_pcis,
    normalize_rat_filter,
    scope_log_label,
)

# PCI资源池
PCI_POOL_4G = list(range(0, 504))
PCI_POOL_5G = list(range(0, 1008))

# 冲突最小安全距离(米): 低于该距离认为需要规避
DEFAULT_SAFE_DISTANCE_M = 1500.0


def _distance_km(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return vincenty_distance(a["lat"], a["lon"], b["lat"], b["lon"]) / 1000.0


def _cell_pool(rat: str) -> List[int]:
    return PCI_POOL_5G if rat == "NR" else PCI_POOL_4G


def _is_conflict(pci_a: int, pci_b: int, rat: str) -> bool:
    conf, _ = check_pair(pci_a, pci_b, rat)
    return conf


def _geo_partition(cells: List[Dict[str, Any]], cells_per_cluster: int = 50) -> List[List[Dict[str, Any]]]:
    """
    简单的经纬度网格分区: 按经纬度均匀划分为若干簇,每簇cell数 <= cells_per_cluster
    实现: 按经纬度排序后滑动窗口聚类
    """
    if not cells:
        return []
    sorted_cells = sorted(cells, key=lambda c: (c.get("lat", 0), c.get("lon", 0)))
    clusters: List[List[Dict[str, Any]]] = []
    for c in sorted_cells:
        placed = False
        for cl in clusters:
            # 贪心加入:与簇内第一个cell距离<5km则加入
            if _distance_km(cl[0], c) < 5.0:
                cl.append(c)
                placed = True
                break
        if not placed:
            clusters.append([c])
    return clusters


def greedy_allocate(
    cells: List[Dict[str, Any]],
    rat: str,
    pool: List[int],
    blacklist: Set[int],
    whitelist: Set[int],
    locked: Dict[str, int],
    safe_distance_km: float = 0.05,
    same_pci_min_km: float = 30.0,
    external_cells: Optional[List[Dict[str, Any]]] = None,
    per_site_thresholds: Optional[Dict[str, Tuple[float, float]]] = None,
    check_mod30: bool = True,
    directional_filter: bool = True,
) -> List[Dict[str, Any]]:
    """
    贪心PCI分配:
    - 遍历小区,候选PCI = 资源池 - 冲突集 - 黑名单 (+白名单优先)
    - 锁定小区直接用其new_pci
    - 评分: 与最近同PCI小区距离最大者胜

    同站小区(<safe_distance_km)强制要求 mod3 不同(mod30同理NR)
    近站(<3km)mod3不同
    同PCI必须距离>=same_pci_min_km
    external_cells: 簇外的同制式小区(用于复用距离评分,不算冲突源)
    per_site_thresholds: {ecgi: (safe_m, same_pci_min_m)} 单小区阈值,
        与全局阈值取并集(更严者)
    check_mod30: 是否对 NR 应用 Mod30 冲突检查
    directional_filter: True=方向背向(双向)的小区对豁免冲突 (缺azimuth/beamwidth 时降级为不过滤)
    """
    assigned = list(cells)

    # 锁定小区: 直接用其PCI,加入已用集
    pci_to_ecgi: Dict[int, str] = {}

    # 第一遍: 处理锁定
    for c in assigned:
        if c["ecgi"] in locked:
            c["new_pci"] = int(locked[c["ecgi"]])
            pci_to_ecgi[c["new_pci"]] = c["ecgi"]

    # 白名单优先候选
    pool_set = set(pool) - blacklist
    if whitelist:
        pool_set = pool_set & whitelist

    # 排序: 按纬度经度,大网格分散
    queue = sorted([c for c in assigned if c["ecgi"] not in locked],
                   key=lambda c: (c.get("lat", 0), c.get("lon", 0)))

    # 簇外小区坐标: 仅用于reuse_score,不参与冲突检测
    external = external_cells or []

    # 工具: 取单小区的 (safe_km, same_pci_min_km) (与全局取并集)
    def _thresholds_for(c: Dict[str, Any]) -> Tuple[float, float]:
        if per_site_thresholds and c["ecgi"] in per_site_thresholds:
            ps_safe_m, ps_same_m = per_site_thresholds[c["ecgi"]]
            return (min(ps_safe_m / 1000.0, safe_distance_km),
                    min(ps_same_m / 1000.0, same_pci_min_km))
        return (safe_distance_km, same_pci_min_km)

    for c in queue:
        cell_safe_km, cell_same_pci_min_km = _thresholds_for(c)
        forbidden: Set[int] = set()
        # 冲突源: 同assigned内其他小区
        for other in assigned:
            if other["ecgi"] == c["ecgi"]:
                continue
            other_pci = other.get("new_pci")
            if other_pci is None:
                continue
            try:
                d_km = _distance_km(c, other)
            except Exception:
                continue

            # 方向性豁免: 双向背向则跳过 (strict mode)
            if directional_filter and mutual_back_facing(c, other):
                continue

            if d_km < cell_safe_km:
                forbidden.update({p for p in pool_set if p % 3 == other_pci % 3})
                if rat == "NR" and check_mod30:
                    forbidden.update({p for p in pool_set if p % 30 == other_pci % 30})
            elif d_km < 3.0:
                forbidden.update({p for p in pool_set if p % 3 == other_pci % 3})
                if rat == "NR" and check_mod30:
                    forbidden.update({p for p in pool_set if p % 30 == other_pci % 30})
            elif d_km < cell_same_pci_min_km:
                forbidden.add(int(other_pci))

        # 簇外小区也参与冲突检测(防跨簇PCI冲突)
        for other in external:
            other_pci = other.get("new_pci") or other.get("pci")
            if other_pci is None:
                continue
            try:
                d_km = _distance_km(c, other)
            except Exception:
                continue
            # 方向性豁免
            if directional_filter and mutual_back_facing(c, other):
                continue
            if d_km < cell_safe_km:
                forbidden.update({p for p in pool_set if p % 3 == other_pci % 3})
                if rat == "NR" and check_mod30:
                    forbidden.update({p for p in pool_set if p % 30 == other_pci % 30})
            elif d_km < 3.0:
                forbidden.update({p for p in pool_set if p % 3 == other_pci % 3})
                if rat == "NR" and check_mod30:
                    forbidden.update({p for p in pool_set if p % 30 == other_pci % 30})
            elif d_km < cell_same_pci_min_km:
                forbidden.add(int(other_pci))

        candidates = [p for p in pool_set if p not in forbidden]

        # 评分: 走统一 PciEvaluator (mod3+mod30+同PCI 三档距离衰减)
        # 这样跨站同方向同 mod3 / 同 mod30 / 同 PCI 全部参与评分, 与单站规划口径一致
        from pci_evaluator import PciEvaluator
        _eval_greedy = PciEvaluator.from_cell(
            c, check_mod30=check_mod30, directional_filter=directional_filter
        )

        def reuse_score(pci: int) -> float:
            # 构造邻居列表 (assigned 内除自身外 + external)
            eval_neighbors: List[Tuple[int, Dict[str, Any], float]] = []
            for other in assigned:
                if other["ecgi"] == c["ecgi"]:
                    continue
                op = other.get("new_pci")
                if op is None:
                    continue
                try:
                    d = _distance_km(c, other)
                except Exception:
                    continue
                if d > max(_eval_greedy.safe_dist_km * 2, _eval_greedy.same_pci_min_km):
                    continue
                eval_neighbors.append((int(op), other, d))
            for other in external:
                op = other.get("new_pci") or other.get("pci")
                if op is None:
                    continue
                try:
                    d = _distance_km(c, other)
                except Exception:
                    continue
                if d > max(_eval_greedy.safe_dist_km * 2, _eval_greedy.same_pci_min_km):
                    continue
                eval_neighbors.append((int(op), other, d))
            return _eval_greedy.score_cell(int(pci), c, eval_neighbors)

        if not candidates:
            forbidden2: Set[int] = set()
            for other in assigned:
                if other["ecgi"] == c["ecgi"]:
                    continue
                other_pci = other.get("new_pci")
                if other_pci is None:
                    continue
                try:
                    d_km = _distance_km(c, other)
                except Exception:
                    continue
                if d_km < safe_distance_km:
                    forbidden2.update({p for p in pool_set if p % 3 == other_pci % 3})
            candidates = [p for p in pool_set if p not in forbidden2]

        if not candidates:
            candidates = sorted(pool_set)

        # 取分数最小的前N (evaluator: 越小越优), 随机选一个以避免每次都选同一个PCI
        scored = [(reuse_score(p), p) for p in candidates]
        scored.sort(key=lambda x: x[0])  # 越小越好
        top_n = min(50, len(scored))
        # 在前N中随机选一个, 分数相同时优先小PCI
        best = random.choice([p for score, p in scored[:top_n]])
        c["new_pci"] = int(best)
        # 最佳PCI + 5个候选
        c["pci_candidates"] = [p for _, p in scored[:5]]

    # ── 硬性 post-check: 同站 mod3 必须各占一位 (4G/5G 通用) ──
    # greedy 是逐扇区贪心, 工程上接受不保证 N_ID(1) 共享 (mod3 隔离即可).
    # N_ID(1) 共享在 _run_pci_planning (整站 SSS 算法) 中保证.
    from pci_sss_constraints import _check_mod3_only
    _check_mod3_only(assigned, pci_field="new_pci", group_by="auto")

    return assigned


def plan_global(
    cells: List[Dict[str, Any]],
    whitelist: Optional[List[int]] = None,
    blacklist: Optional[List[int]] = None,
    locked: Optional[Dict[str, int]] = None,
    per_site_thresholds: Optional[Dict[str, Tuple[float, float]]] = None,
    check_mod30: bool = True,
    directional_filter: bool = True,
    reuse_distance_km: float = 5.0,
    rat_filter: Optional[str] = None,
    freq_band_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    第一级 全网粗规划
    1. 按制式分组
    2. 每制式按地理分簇,簇内贪心分配
    3. 簇间保留独立资源池
    rat_filter / freq_band_filter: 仅对匹配小区分配 PCI，其余已在 locked 中
  external_cells 使用全网小区作冲突参考
    """
    wl = set(whitelist or [])
    bl = set(blacklist or [])
    lk = dict(locked or {})

    log: List[str] = []
    log.append(f"[粗规划] 输入小区: {len(cells)}")
    scope_label = scope_log_label(rat_filter, freq_band_filter)
    if rat_filter or (freq_band_filter and str(freq_band_filter).strip()):
        in_scope = filter_cells_pci_scope(cells, rat_filter, freq_band_filter)
        log.append(f"[粗规划] 规划范围: {scope_label} → {len(in_scope)} 个待分配")
    if directional_filter:
        log.append("[粗规划] 方向性过滤: 启用 (背向小区对豁免)")

    rat_norm = normalize_rat_filter(rat_filter)
    fb_norm = normalize_freq_band(str(freq_band_filter).strip()) if freq_band_filter and str(freq_band_filter).strip() else None

    # 制式分组（有 rat_filter 时只跑该制式）
    by_rat: Dict[str, List[Dict[str, Any]]] = {}
    for c in cells:
        if is_nb_znh_cell(c):
            continue
        r = c.get("rat", "LTE")
        if rat_norm and r != rat_norm:
            continue
        by_rat.setdefault(r, []).append(c)

    for rat, group in by_rat.items():
        if fb_norm:
            group = [c for c in group if cell_freq_band_key(c) == fb_norm]
            if not group:
                continue
        # ── 同站多扇区预分配 (SSS 算法, 4G/5G 一致, 保证 N_ID(1) 共享) ──
        from pci_sss_constraints import preassign_same_site_sss
        sss_locked = preassign_same_site_sss(group, rat, reuse_distance_km=reuse_distance_km)
        if sss_locked:
            log.append(f"[粗规划] {rat}: SSS 预分配 {len(sss_locked)} 小区 (同站 N_ID(1) 共享)")
        merged_locked = {**lk, **sss_locked}

        clusters = _geo_partition(group, cells_per_cluster=50)
        log.append(f"[粗规划] {rat}: {len(group)}小区 -> {len(clusters)}簇")
        for idx, cl in enumerate(clusters):
            cluster_ecgis = {c["ecgi"] for c in cl}
            # 冲突参考：全网同制式（+同频段若指定），不仅簇内
            other_pool = [c for c in cells if c.get("rat") == rat and not is_nb_znh_cell(c)]
            if fb_norm:
                other_pool = [c for c in other_pool if cell_freq_band_key(c) == fb_norm]
            other_cells = [c for c in other_pool if c["ecgi"] not in cluster_ecgis]
            cluster_locked = {k: v for k, v in merged_locked.items() if k in cluster_ecgis}
            cells_to_assign = [
                c for c in cl
                if c["ecgi"] not in sss_locked
                and cell_matches_pci_scope(c, rat_filter, freq_band_filter)
            ]
            if not cells_to_assign:
                continue
            greedy_allocate(
                cells_to_assign, rat, _cell_pool(rat), bl, wl, cluster_locked,
                external_cells=other_cells,
                per_site_thresholds=per_site_thresholds,
                check_mod30=check_mod30,
                directional_filter=directional_filter,
            )
            log.append(f"  簇{idx+1}: {len(cells_to_assign)}小区 PCI分配完成")

    return {"cells": cells, "log": log}


def plan_partial(
    cells: List[Dict[str, Any]],
    selected_ecgis: List[str],
    radius_km: float = 5.0,
    whitelist: Optional[List[int]] = None,
    blacklist: Optional[List[int]] = None,
    directional_filter: bool = True,
    per_site_thresholds: Optional[Dict[str, Tuple[float, float]]] = None,
    check_mod30: bool = True,
    rat_filter: Optional[str] = None,
    freq_band_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    第二级 局部微调:
    - 框选小区集合 -> 仅重算该集合 + 周边辐射半径内小区
    - 未受影响的小区保留原PCI(若new_pci已存在则保留)
    rat_filter / freq_band_filter: 仅重算匹配制式/频段的小区
    """
    wl = set(whitelist or [])
    bl = set(blacklist or [])
    log: List[str] = []
    matched_sel, skipped_sel = filter_ecgis_pci_scope(
        cells, selected_ecgis, rat_filter, freq_band_filter,
    )
    if skipped_sel:
        log.append(f"[局部微调] 制式/频段过滤: 跳过 {len(skipped_sel)} 个框选小区")
    if not matched_sel:
        log.append("[局部微调] 无符合制式/频段条件的小区")
        return {
            "cells": cells,
            "log": log,
            "affected": [],
            "stats": {"total": len(cells), "conflict_count": 0, "engine": "legacy", "local_mode": True},
            "conflicts": [],
        }
    selected_ecgis = matched_sel
    log.append(f"[局部微调] 框选: {len(selected_ecgis)}小区, 辐射半径: {radius_km}km, 范围: {scope_log_label(rat_filter, freq_band_filter)}")
    if directional_filter:
        log.append("[局部微调] 方向性过滤: 启用 (背向小区对豁免)")

    selected_set = set(selected_ecgis)
    targets: Set[str] = set(selected_set)
    for c in cells:
        if c["ecgi"] in selected_set:
            continue
        for s in selected_ecgis:
            sel = next((x for x in cells if x["ecgi"] == s), None)
            if not sel:
                continue
            try:
                if _distance_km(c, sel) <= radius_km:
                    targets.add(c["ecgi"])
            except Exception:
                continue

    log.append(f"[局部微调] 影响范围: {len(targets)}小区")

    target_cells = [c for c in cells if c["ecgi"] in targets]

    # 制式分组
    by_rat: Dict[str, List[Dict[str, Any]]] = {}
    for c in target_cells:
        by_rat.setdefault(c.get("rat", "LTE"), []).append(c)

    # 锁定: 未被影响的cell保留new_pci(若已有)
    locked: Dict[str, int] = {}
    for c in cells:
        if c["ecgi"] not in targets and "new_pci" in c:
            locked[c["ecgi"]] = int(c["new_pci"])

    for rat, group in by_rat.items():
        to_assign = [c for c in group if cell_matches_pci_scope(c, rat_filter, freq_band_filter)]
        if not to_assign:
            continue
        greedy_allocate(
            to_assign, rat, _cell_pool(rat), bl, wl, locked,
            same_pci_min_km=max(radius_km, 30.0),
            external_cells=[c for c in cells if c["ecgi"] not in targets and not is_nb_znh_cell(c)],
            per_site_thresholds=per_site_thresholds,
            check_mod30=check_mod30,
            directional_filter=directional_filter,
        )
        log.append(f"  {rat}: {len(to_assign)}小区 重新分配")

    # 局部微调后做一次目标范围冲突统计
    conflicts = collect_conflicts(cells, use_original_pci=False, directional_filter=directional_filter)
    target_ecgi_set = set(targets)
    local_conflicts = [
        c for c in conflicts
        if c["a"]["ecgi"] in target_ecgi_set or c["b"]["ecgi"] in target_ecgi_set
    ]
    log.append(f"[局部微调] 目标相关冲突: {len(local_conflicts)}")

    return {
        "cells": cells,
        "log": log,
        "affected": list(targets),
        "stats": {
            "total": len(cells),
            "conflict_count": len(local_conflicts),
            "engine": "legacy",
            "local_mode": True,
        },
        "conflicts": local_conflicts,
    }


def plan_verify_and_fix(
    cells: List[Dict[str, Any]],
    directional_filter: bool = True,
    rat_filter: Optional[str] = None,
    freq_band_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    第三级 全局二次校验 + 轻度冲突自动修复
    - 全量检测
    - 同站点分组: 同站点(<0.3km)作为一个整体, PCI一起调整保证mod3(mod30)不同
    - 轻度冲突: 影响小区数<=3, 自动修复(贪心换PCI)
    - 重度冲突: 输出清单供人工处理
    directional_filter: 透传到 collect_conflicts 与 greedy_allocate
    """
    log: List[str] = []
    log.append("[校验] 开始全网PCI冲突检测")
    if directional_filter:
        log.append("[校验] 方向性过滤: 启用 (背向小区对豁免)")

    # 先全部基于new_pci构建索引
    conflicts = collect_conflicts(cells, use_original_pci=False,
                                   directional_filter=directional_filter)
    log.append(f"[校验] 检出冲突: {len(conflicts)}")

    # 同站分组: 距离<0.3km视为同站
    site_groups: Dict[str, List[str]] = {}
    for c in cells:
        site = c.get("site_name") or c.get("name", "").rsplit("-", 1)[0] or c.get("ecgi", "")
        if not site:
            # 用坐标分桶
            lat_bucket = round(c.get("lat", 0), 3)
            lon_bucket = round(c.get("lon", 0), 3)
            site = f"_auto_{lat_bucket}_{lon_bucket}"
        site_groups.setdefault(site, []).append(c["ecgi"])

    def ecgi_to_site(ecgi: str) -> str:
        for s, ecgis in site_groups.items():
            if ecgi in ecgis:
                return s
        return ""

    # 按小区统计被冲突次数
    impact_count: Dict[str, int] = {}
    for c in conflicts:
        for k in ("a", "b"):
            ecgi = c[k]["ecgi"]
            impact_count[ecgi] = impact_count.get(ecgi, 0) + 1

    # 同站点整体影响数
    def site_impact(ecgi: str) -> int:
        s = ecgi_to_site(ecgi)
        return sum(impact_count.get(e, 0) for e in site_groups.get(s, []))

    fixed = 0
    fixed_sites: Set[str] = set()
    remaining: List[Dict[str, Any]] = []

    for conf in conflicts:
        a_ecgi = conf["a"]["ecgi"]
        b_ecgi = conf["b"]["ecgi"]
        a_site = ecgi_to_site(a_ecgi)
        b_site = ecgi_to_site(b_ecgi)
        # 修复条件: 任一方所在站点整体影响数 <= 3 (轻度)
        if site_impact(a_ecgi) <= 3 or site_impact(b_ecgi) <= 3:
            # 选择影响少的站点整体重分配
            target_site = a_site if site_impact(a_ecgi) <= site_impact(b_ecgi) else b_site
            if target_site in fixed_sites:
                # 同一站点已修复过,跳过
                continue
            fixed_sites.add(target_site)
            target_ecgis = site_groups[target_site]
            targets = [
                c for c in cells if c["ecgi"] in target_ecgis
                and cell_matches_pci_scope(c, rat_filter, freq_band_filter)
            ]
            if not targets:
                continue
            others = [c for c in cells if c["ecgi"] not in {t["ecgi"] for t in targets}]
            locked_dict = {o["ecgi"]: int(o["new_pci"]) for o in others if "new_pci" in o}
            new_cells = greedy_allocate(targets, targets[0].get("rat", "LTE"),
                                        _cell_pool(targets[0].get("rat", "LTE")),
                                        set(), set(), locked_dict,
                                        external_cells=others,
                                        directional_filter=directional_filter)
            if new_cells:
                fixed += 1
        else:
            remaining.append(conf)

    # 再校验一次
    conflicts_after = collect_conflicts(cells, use_original_pci=False,
                                         directional_filter=directional_filter)
    log.append(f"[校验] 自动修复站点: {fixed}个, 剩余冲突: {len(conflicts_after)}")

    # ── 硬性 post-check: 同站 N_ID(1) 共享 + mod3 分布 (4G/5G 通用) ──
    # 全网规划已通过 plan_global 的 SSS 预分配保证 N_ID(1) 共享;
    # 此处作为最终保险, 违反则 raise.
    from pci_sss_constraints import assert_same_site_sss_shared
    assert_same_site_sss_shared(cells, pci_field="new_pci", group_by="auto")
    log.append("[校验] 同站 N_ID(1) / mod3 校验通过 ✓")

    return {
        "log": log,
        "fixed_count": fixed,
        "conflicts": conflicts_after,
    }


def plan_all(cells: List[Dict[str, Any]],
             whitelist: Optional[List[int]] = None,
             blacklist: Optional[List[int]] = None,
             locked: Optional[Dict[str, int]] = None,
             engine: str = "legacy",
             reuse_distance_km: float = 5.0,
             check_mod6: bool = False,
             check_mod30: bool = True,
             per_site_thresholds: Optional[Dict[str, Tuple[float, float]]] = None,
             # ── 局部模式: 仅对 target_indices 小区分配 PCI，跳过全量验证 ──
             target_indices: Optional[List[int]] = None,
             # ── 方向性过滤: 背向小区对豁免同PCI/mod3/mod30 冲突 ──
             directional_filter: bool = True,
             rat_filter: Optional[str] = None,
             freq_band_filter: Optional[str] = None) -> Dict[str, Any]:
    """
    三级规划串联入口.

    target_indices: 若非空，仅对这些索引分配 PCI；其余视为 locked（局部规划模式）。
    rat_filter / freq_band_filter: 仅对匹配小区重算 PCI，其余锁定。
    """
    log: List[str] = []
    local_mode = target_indices is not None
    lk = lock_out_of_scope_pcis(cells, rat_filter, freq_band_filter, locked)
    if rat_filter or (freq_band_filter and str(freq_band_filter).strip()):
        n_scope = len(filter_cells_pci_scope(cells, rat_filter, freq_band_filter))
        log.append(f"[PCI] 规划范围: {scope_log_label(rat_filter, freq_band_filter)} ({n_scope} 小区)")

    if engine == "rftools":
        from pci_rsi_planner import pci_rsi_plan
        if not local_mode:
            clear_new_pci_in_scope(cells, rat_filter, freq_band_filter, lk)
        r_rft = pci_rsi_plan(
            cells,
            reuse_distance_km=reuse_distance_km,
            check_mod3=True,
            check_mod6=check_mod6,
            check_mod30=check_mod30,
            directional_filter=directional_filter,
            rat_filter=rat_filter,
            freq_band_filter=freq_band_filter,
            locked_pcis=lk,
        )
        log.extend(r_rft["log"])
        if not local_mode:
            conflicts = collect_conflicts(cells, use_original_pci=False,
                                           directional_filter=directional_filter)
            return {
                "cells": cells, "log": log, "conflicts": conflicts,
                "stats": {"total": len(cells), "conflict_count": len(conflicts),
                          "fixed_count": 0, "engine": "rftools"},
            }
        else:
            log.append(f"[PCI] 局部模式: 仅校验 {len(target_indices)} 个目标小区")
            return {
                "cells": cells, "log": log,
                "stats": {"total": len(cells), "engine": "rftools", "local_mode": True},
            }

    # legacy (默认)
    if not local_mode:
        clear_new_pci_in_scope(cells, rat_filter, freq_band_filter, lk)
    elif target_indices is not None:
        for i in target_indices:
            c = cells[i]
            if not cell_matches_pci_scope(c, rat_filter, freq_band_filter):
                continue
            if c.get("ecgi") not in lk and "new_pci" in c:
                del c["new_pci"]

    # 第一级: plan_global 已通过 locked dict 约束了新小区的 PCI
    r1 = plan_global(
        cells, whitelist, blacklist, lk,
        per_site_thresholds=per_site_thresholds,
        check_mod30=check_mod30,
        directional_filter=directional_filter,
        reuse_distance_km=reuse_distance_km,
        rat_filter=rat_filter,
        freq_band_filter=freq_band_filter,
    )
    log.extend(r1["log"])

    if local_mode:
        # 局部模式: 仅验证 target_indices 小区间的冲突（O(target²)，而非 O(N²)）
        target_set = set(target_indices)
        log.append(f"[PCI] 局部验证: {len(target_indices)} 个目标小区")
        conflicts = [
            c for c in collect_conflicts(cells, use_original_pci=False,
                                          directional_filter=directional_filter)
            if c["a"]["ecgi"] in {cells[i]["ecgi"] for i in target_indices}
            or c["b"]["ecgi"] in {cells[i]["ecgi"] for i in target_indices}
        ]
        log.append(f"[PCI] 局部冲突: {len(conflicts)} (目标小区相关)")
        return {
            "cells": cells, "log": log, "conflicts": conflicts,
            "stats": {"total": len(cells), "conflict_count": len(conflicts),
                      "fixed_count": 0, "engine": "legacy", "local_mode": True},
        }

    # 全量模式: 三级验证
    r3 = plan_verify_and_fix(
        cells,
        directional_filter=directional_filter,
        rat_filter=rat_filter,
        freq_band_filter=freq_band_filter,
    )
    log.extend(r3["log"])

    conflicts = collect_conflicts(cells, use_original_pci=False,
                                   directional_filter=directional_filter)
    return {
        "cells": cells, "log": log, "conflicts": conflicts,
        "stats": {"total": len(cells), "conflict_count": len(conflicts),
                  "fixed_count": r3.get("fixed_count", 0), "engine": "legacy"},
    }