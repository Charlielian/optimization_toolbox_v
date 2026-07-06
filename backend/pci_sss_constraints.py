"""
同站 SSS / N_ID(1) 共享约束

背景:
- LTE 与 NR 共用 PCI 资源编号空间 (PCI = 3 × N_ID(1) + N_ID(2))
- 同站所有扇区必须共享同一个 N_ID(1), 各扇区 mod3 必须各占 0/1/2 (4G/5G 通用)
- 本模块提供:
  1. 同站识别工具 (按 site_name 或经纬度聚类)
  2. 硬性 post-check: 违反 N_ID(1) 共享约束直接 raise RuntimeError
  3. 同站预分组工具 (供 legacy / rftools 引擎在分配时使用)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


def nid1_of(pci: int) -> int:
    """PCI -> N_ID(1) (PCI = 3*N_ID(1) + N_ID(2))"""
    return int(pci) // 3


def nid2_of(pci: int) -> int:
    """PCI -> N_ID(2)"""
    return int(pci) % 3


def group_cells_by_site(cells: List[Dict[str, Any]],
                         coord_round: int = 6) -> Dict[Tuple, List[Dict[str, Any]]]:
    """
    按 (site_name 或 (lat, lon) 分桶) 把小区分到同站组
    - 优先 site_name 字段
    - 缺失则用 (round(lat, coord_round), round(lon, coord_round)) 作为兜底
    """
    groups: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for c in cells:
        site = c.get("site_name")
        if not site:
            lat = c.get("lat", 0.0)
            lon = c.get("lon", 0.0)
            try:
                site_key = (round(float(lat), coord_round), round(float(lon), coord_round))
            except (TypeError, ValueError):
                site_key = ("_invalid_", c.get("ecgi", "?"))
        else:
            site_key = ("site", str(site))
        groups[site_key].append(c)
    return groups


def _check_mod3_only(cells: List[Dict[str, Any]],
                     pci_field: str = "new_pci",
                     group_by: str = "auto") -> None:
    """
    工程宽松校验: 只校验同站 mod3 必须各占一位, 不要求 N_ID(1) 共享.
    用于 legacy greedy_allocate (逐扇区贪心, 工程上接受 N_ID(1) 不一致).

    违反时 raise RuntimeError.
    """
    groups = group_cells_by_site(cells)
    errors: List[str] = []
    for site_key, group in groups.items():
        if len(group) < 2:
            continue
        mod3s: set = set()
        ecgi_pci_pairs: List[Tuple[str, int]] = []
        for c in group:
            pci_v = c.get(pci_field)
            if pci_v is None:
                continue
            pci_int = int(pci_v)
            mod3s.add(nid2_of(pci_int))
            ecgi_pci_pairs.append((c.get("ecgi", "?"), pci_int))
        if not ecgi_pci_pairs:
            continue
        if len(group) >= 3 and mod3s != {0, 1, 2}:
            errors.append(
                f"同站 {site_key} 违反 mod3 分布: "
                f"扇区数={len(group)}, mod3 集={sorted(mod3s)}, "
                f"应为 {{0,1,2}}, 详情: {ecgi_pci_pairs}"
            )
        elif len(group) == 2 and len(mod3s) != 2:
            errors.append(
                f"同站 {site_key} 违反 mod3 不同: "
                f"扇区数=2, mod3 集={sorted(mod3s)}, 应互不相同, "
                f"详情: {ecgi_pci_pairs}"
            )

    if errors:
        msg = "[mod3约束] 同站 mod3 校验失败:\n  - " + "\n  - ".join(errors)
        raise RuntimeError(msg)


def assert_same_site_sss_shared(cells: List[Dict[str, Any]],
                                 pci_field: str = "new_pci",
                                 group_by: str = "auto") -> None:
    """
    硬性校验: 同站所有扇区必须 N_ID(1) 共享 + mod3 各占一位 (0/1/2).

    group_by:
      - 'site_name'  : 按 site_name 分组
      - 'coord'      : 按 (lat, lon) 坐标分桶
      - 'auto'       : 优先 site_name, 缺失时降级到 coord

    违反时 raise RuntimeError (调用方应捕获并返回 500).
    """
    if group_by == "site_name":
        groups = group_cells_by_site(cells)
        # 转为只用 site_name 维度
        groups = {("site", k[1]): v for k, v in groups.items() if k[0] == "site"}
        coord_only = group_cells_by_site(cells)
        # 兜底: 把无 site_name 的也按 coord 分
        for c in cells:
            if not c.get("site_name"):
                lat = round(float(c.get("lat", 0.0)), 6)
                lon = round(float(c.get("lon", 0.0)), 6)
                key = ("coord", lat, lon)
                if key not in groups:
                    groups[key] = [c]
                else:
                    if c not in groups[key]:
                        groups[key].append(c)
    else:
        groups = group_cells_by_site(cells)

    errors: List[str] = []
    for site_key, group in groups.items():
        # 工参库中同一 ECGI 重复行只计一次（常见于批量规划重复上传）
        _seen_ecgi: set = set()
        _deduped: List[Dict[str, Any]] = []
        for c in group:
            e = c.get("ecgi")
            if e and e in _seen_ecgi:
                continue
            if e:
                _seen_ecgi.add(e)
            _deduped.append(c)
        group = _deduped
        if len(group) < 2:
            continue  # 单扇区站不需要检查
        # 收集已分配的 N_ID(1) 和 mod3
        nid1s: set = set()
        mod3s: set = set()
        ecgi_pci_pairs: List[Tuple[str, int]] = []
        for c in group:
            pci_v = c.get(pci_field)
            if pci_v is None:
                continue
            pci_int = int(pci_v)
            n1 = nid1_of(pci_int)
            m3 = nid2_of(pci_int)
            nid1s.add(n1)
            mod3s.add(m3)
            ecgi_pci_pairs.append((c.get("ecgi", "?"), pci_int))
        if not ecgi_pci_pairs:
            continue
        if len(nid1s) > 1:
            errors.append(
                f"同站 {site_key} 违反 N_ID(1) 共享约束: "
                f"N_ID(1) 不一致 {sorted(nid1s)}, 详情: {ecgi_pci_pairs}"
            )
        # 多扇区且 mod3 不全: 仅当扇区数 >= 3 时严格要求 0/1/2 各一;
        # 2 扇区时只需 mod3 不同
        if len(group) >= 3 and mod3s != {0, 1, 2}:
            errors.append(
                f"同站 {site_key} 违反 mod3 分布: "
                f"扇区数={len(group)}, mod3 集={sorted(mod3s)}, "
                f"应为 {{0,1,2}}, 详情: {ecgi_pci_pairs}"
            )
        elif len(group) == 2 and len(mod3s) != 2:
            errors.append(
                f"同站 {site_key} 违反 mod3 不同: "
                f"扇区数=2, mod3 集={sorted(mod3s)}, 应互不相同, "
                f"详情: {ecgi_pci_pairs}"
            )

    if errors:
        msg = "[SSS约束] 同站 N_ID(1) / mod3 校验失败:\n  - " + "\n  - ".join(errors)
        raise RuntimeError(msg)


def enforce_same_site_mod3(cells: List[Dict[str, Any]],
                            pci_field: str = "new_pci") -> None:
    """
    分配完成后调用: 对每组 cell (按 site_name 或坐标聚类) 做 mod3 校验
    - 扇区数 >= 2 时, mod3 必须互不相同
    - N_ID(1) 必须共享

    这是 assert_same_site_sss_shared 的语义糖 (默认用 auto 分组).
    """
    assert_same_site_sss_shared(cells, pci_field=pci_field, group_by="auto")


def _pick_sss_group(site_cells: List[Dict[str, Any]],
                     rat: str,
                     nearby_pcis: List[Tuple[int, float]],
                     score_cap: float,
                     safe_dist_km: Optional[float] = None,
                     same_pci_min_km: Optional[float] = None) -> int:
    """
    为一个同站组挑选最佳 N_ID(1).

    评分: 走统一评估器 PciEvaluator.score_group, 综合 mod3/mod30/同PCI 三档距离衰减.
    - 4G: N_ID(1) ∈ [0, 167]
    - 5G: N_ID(1) ∈ [0, 335]
    - 评分: 越低越好 (0 = 无冲突)
    """
    from pci_evaluator import PciEvaluator, pick_best_nid1

    # 频段自适应阈值 (调用方注入, 否则默认 700M 宏站)
    safe_km = safe_dist_km if safe_dist_km is not None else 5.0
    same_pci_min = same_pci_min_km if same_pci_min_km is not None else 30.0

    nid1_max = 167 if rat == "LTE" else 335
    n_sectors = len(site_cells)

    evaluator = PciEvaluator(
        safe_dist_km=safe_km,
        same_pci_min_km=same_pci_min,
        check_mod30=(rat == "NR"),
        directional_filter=True,
    )

    # 把 (pci, dist_km) 转换成 evaluator 期望的 (pci, cell_dict, dist_km) 三元组
    # site_cells 是该站所有扇区; 每个扇区到 nearby_pcis 的距离都需要 (PCI 距离是 distance, 不区分方位)
    # 这里假设 nearby_pcis 已经按 site center 计算 (即每个扇区到 neighbor 距离近似一致)
    # 对 nid1 选择而言, site 内 nid1 不变, 用 site center 距离足够精确
    site_lat = sum(c.get("lat", 0) for c in site_cells) / max(1, len(site_cells))
    site_lon = sum(c.get("lon", 0) for c in site_cells) / max(1, len(site_cells))
    target_cells_template = [{
        "lat": c.get("lat", site_lat),
        "lon": c.get("lon", site_lon),
        "azimuth": c.get("azimuth", 0),
        "beamwidth": c.get("beamwidth", c.get("beam", 65)),
    } for c in site_cells]

    # nearby_pcis 转 (pci, cell_dict, dist_km)
    # cell_dict 用占位 {lat:site_lat, lon:site_lon} 因为我们关心距离评分, 方向用 site 内方位
    neighbors: List[Tuple[int, Dict[str, Any], float]] = []
    for pci, d_km in nearby_pcis:
        neighbors.append((int(pci), {
            "lat": site_lat, "lon": site_lon,
            "azimuth": 0, "beamwidth": 360,  # 占位: 全向 (不对 site 内判定背向)
        }, float(d_km)))

    return pick_best_nid1(nid1_max, n_sectors, neighbors,
                          target_cells_template, evaluator)


def preassign_same_site_sss(cells: List[Dict[str, Any]],
                              rat: str,
                              reuse_distance_km: float = 5.0,
                              pci_field: str = "new_pci",
                              group_by: str = "auto",
                              safe_dist_km: Optional[float] = None,
                              same_pci_min_km: Optional[float] = None) -> Dict[str, int]:
    """
    对同站多扇区组 (>=2 扇区) 走整站 SSS 算法预分配:
      - 4G: N_ID(1) ∈ [0, 167]
      - 5G: N_ID(1) ∈ [0, 335]
    写 cell[pci_field] 并返回 {ecgi: preassigned_pci} 给后续 greedy / rftools 用作 locked.

    单扇区站 / 异站不影响.

    参数:
      reuse_distance_km: 用于计算 nearby_pcis 的同 PCI 复用距离
      pci_field: 写入字段, 默认 "new_pci"
      safe_dist_km: 同 mod3 安全距离 (频段自适应, 默认由调用方注入)
      same_pci_min_km: 同 PCI 最小复用距离 (频段自适应, 默认由调用方注入)
    """
    from geo_utils import vincenty_distance
    from pci_evaluator import PciEvaluator, pick_best_nid1

    groups = group_cells_by_site(cells)
    locked_out: Dict[str, int] = {}

    for site_key, group in groups.items():
        if len(group) < 2:
            continue

        # 收集 nearby PCIs: 复用距离内的其他小区 PCI + 距离
        nearby_pcis: List[Tuple[int, float]] = []
        site_lat = sum(c.get("lat", 0) for c in group) / len(group)
        site_lon = sum(c.get("lon", 0) for c in group) / len(group)
        for other in cells:
            if other in group:
                continue
            other_pci = other.get(pci_field) or other.get("pci")
            if other_pci is None or int(other_pci) < 0:
                continue
            try:
                d_km = vincenty_distance(site_lat, site_lon,
                                          float(other["lat"]), float(other["lon"])) / 1000.0
            except (KeyError, TypeError, ValueError):
                continue
            if d_km <= reuse_distance_km:
                nearby_pcis.append((int(other_pci), d_km))

        # 频段自适应阈值: 从 group 中第一个 cell 取 (plan_site_type, freq_band)
        # 若调用方未注入, fallback 到 group 第一 cell 的频段
        first = group[0]
        _safe, _same_pci = PciEvaluator.from_cell(first, check_mod30=(rat == "NR")).safe_dist_km, \
                          PciEvaluator.from_cell(first, check_mod30=(rat == "NR")).same_pci_min_km
        eff_safe = safe_dist_km if safe_dist_km is not None else _safe
        eff_same_pci = same_pci_min_km if same_pci_min_km is not None else _same_pci

        # 选 nid1 (走 PciEvaluator)
        best_nid1 = _pick_sss_group(
            group, rat, nearby_pcis, score_cap=reuse_distance_km,
            safe_dist_km=eff_safe,
            same_pci_min_km=eff_same_pci,
        )

        # 写 PCI: 各扇区 mod3 = i % 3
        for i, c in enumerate(group):
            target_mod = i % 3
            forced_pci = best_nid1 * 3 + target_mod
            c[pci_field] = int(forced_pci)
            locked_out[c["ecgi"]] = int(forced_pci)

    return locked_out