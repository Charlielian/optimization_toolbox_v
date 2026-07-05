"""
RFTools 风格扇区干扰分析 (按 4G/5G + freq_band 严格分桶)
移植自 https://github.com/mbebs/RFTools/blob/master/interference_analysis_dialog.py

三检 (同 (rat, freq_band, EARFCN) 桶内):
  - 同频 (Co-Channel):   Δf<0.1
  - 邻频 (Adjacent Channel): Δf ∈ [5, 20] (同 rat, 同 freq_band)
  - PCI 冲突 (collision / mod3 / mod6): (rat, freq_band, EARFCN) 桶内

输出(对每个小区):
  - score 0..100:     累加严重度×距离衰减,反映综合干扰
  - same_pci_min_km:  同 (rat, freq_band, EARFCN) 桶内与本小区同 PCI 的最近距离 (无则 null)
  - issues_count:     该小区参与的 issue 总数
  - top_issues:       最严重的几条 issue 摘要
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from geo_utils import point_in_area, vincenty_distance


# 1° 经度在中低纬度约 111km
KM_PER_DEG = 111.0

# 严重度 -> 权重(配合距离衰减累加, ×10 后钳到 0-100)
_SEVERITY_WEIGHT = {"Critical": 12.0, "High": 6.0, "Medium": 2.5, "Low": 1.0}


# ─────────────────────────────────────────────────────────────────
# 空间网格工具 (削减 O(n²) 配对)
# ─────────────────────────────────────────────────────────────────
def _build_grid_index(
    sectors: List[Dict[str, Any]],
    cell_deg: float,
) -> Dict[Tuple[int, int], List[Dict[str, Any]]]:
    """用 lat/lon 网格建立索引, cell_deg 度/格。"""
    grid: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for s in sectors:
        lat, lon = s["point"]
        gx = int(lon / cell_deg)
        gy = int(lat / cell_deg)
        grid.setdefault((gx, gy), []).append(s)
    return grid


def _quick_dist_km_ub(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
) -> float:
    """经纬度差粗估 km 上界, 保证 ≥ 真实距离 (L∞ 范数)。"""
    lat1, lon1 = p1
    lat2, lon2 = p2
    dlat_km = abs(lat2 - lat1) * KM_PER_DEG
    avg_lat = math.radians((lat1 + lat2) * 0.5)
    cos_mid = max(0.01, math.cos(avg_lat))
    dlon_km = abs(lon2 - lon1) * KM_PER_DEG * cos_mid
    return max(dlat_km, dlon_km)


def _distance_km_point(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return vincenty_distance(a[0], a[1], b[0], b[1]) / 1000.0


def _bearing(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """真方位角 (正北 0, 顺时针 0-360)"""
    lat1, lon1 = a
    lat2, lon2 = b
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    theta = math.degrees(math.atan2(y, x))
    return (theta + 360) % 360


def _calculate_beam_overlap(azimuth: float, beamwidth: float, bearing: float) -> float:
    """RFTools 风格: 100% - 线性衰减/0% (扇区波束与对端方向夹角的重叠百分比)"""
    diff = abs(((bearing - azimuth + 540) % 360) - 180)  # [0..180]
    hw = max(1.0, beamwidth / 2.0)
    if diff <= hw:
        return 100.0
    if diff >= 180 - 1e-9:
        return 0.0
    # 线性衰减: 在 [-hw, +(180-hw)] 之间线性降到 0
    # 0% 出现在 azimuth 波束外 180° 内一侧
    if diff < 180 - hw:
        # 还在另一扇区波束外的有效范围, 这里 RFTools 视为 0
        return 0.0
    # 在扇区背面, 线性插值
    return max(0.0, min(100.0, 100.0 * (180 - diff) / max(1.0, hw)))


def _safe_float(v: Any, d: float = 0.0) -> float:
    try:
        return float(v) if v is not None and v != "" else d
    except (TypeError, ValueError):
        return d


def _safe_int(v: Any, d: int = -1) -> int:
    try:
        if v is None or v == "":
            return d
        return int(float(v))
    except (TypeError, ValueError):
        return d


# ─────────────────────────────────────────────────────────────────
# Cells → sectors, 在同一 (rat, freq_band) 桶内做 RFTools 检测
# ─────────────────────────────────────────────────────────────────
def _build_sectors(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把原始 cells 归一为 sector 字典, _stats / _inbox 累计 per-cell 数据"""
    out: List[Dict[str, Any]] = []
    for c in cells:
        lat = _safe_float(c.get("lat"))
        lon = _safe_float(c.get("lon"))
        if not (isinstance(lat, float) and isinstance(lon, float) and -90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        rat_raw = str(c.get("rat") or "LTE").strip().upper()
        if rat_raw in ("LTE", "4G", "EUTRA", "EUTRAN"):
            rat_norm = "LTE"
        elif rat_raw in ("NR", "5G", "5G NR", "GNB"):
            rat_norm = "NR"
        else:
            rat_norm = rat_raw
        ecgi = str(c.get("ecgi") or "")
        if not ecgi:
            continue
        sector_id = ecgi
        out.append({
            "cell": c,
            "point": (lat, lon),
            "frequency": _safe_float(c.get("earfcn") or c.get("frequency"), 0.0),
            "pci": _safe_int(c.get("new_pci") if c.get("new_pci") is not None else c.get("pci"), -1),
            "band": str(c.get("freq_band") or c.get("band") or "unknown"),
            "azimuth": _safe_float(c.get("azimuth"), 0.0),
            "beamwidth": _safe_float(c.get("beamwidth") or c.get("beam"), 65.0),
            "sector_id": sector_id,
            "rat": rat_norm,
            # per-cell 评估累加 (就地累计, 不依赖 issue 裁剪)
            "_inbox": [],     # list of {type, severity, partner_ecgi, distance_km, overlap_pct}
            "_weighted": 0.0, # sum(severity_weight × distance_decay)
            "same_pci_min_km": None,
            "same_pci_min_partner": None,
        })
    return out


# ─────────────────────────────────────────────────────────────────
# cell 是否落在区域 area 内 (rect / circle / polygon)
def _in_area(point: Tuple[float, float], area: Optional[Dict[str, Any]]) -> bool:
    lat, lon = point
    return point_in_area(lat, lon, area)


def filter_sectors_by_area(
    sectors: List[Dict[str, Any]],
    area: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """只保留落在 area 内的 sector"""
    if not area:
        return sectors
    return [s for s in sectors if _in_area(s["point"], area)]


# ─────────────────────────────────────────────────────────────────
# 把一次 (s1, s2) issue 对开双方 inbox 都累加一份
# ─────────────────────────────────────────────────────────────────
def _add_inbox(
    sector: Dict[str, Any],
    partner: Dict[str, Any],
    itype: str,
    severity: str,
    distance_km: float,
    overlap_pct: float,
    max_distance_km: float,
    top_n: int = 3,
) -> None:
    sector["_inbox"].append({
        "type": itype,
        "severity": severity,
        "partner_ecgi": partner["sector_id"],
        "distance_km": round(distance_km, 3),
        "overlap_pct": round(max(overlap_pct, 0.0), 1),
    })
    w = _SEVERITY_WEIGHT.get(severity, 1.0)
    decay = 1.0 / (1.0 + distance_km / max(1e-3, max_distance_km))
    sector["_weighted"] += w * decay

    # top_n 维护: 仅 Critical / High 才值得 top, 中等以下冗余
    if severity in ("Critical", "High"):
        sector.setdefault("_top", [])
        if len(sector["_top"]) < top_n:
            sector["_top"].append({
                "type": itype,
                "severity": severity,
                "partner_ecgi": partner["sector_id"],
                "distance_km": round(distance_km, 3),
                "overlap_pct": round(max(overlap_pct, 0.0), 1),
            })


# ─────────────────────────────────────────────────────────────────
# 同频: (rat, freq_band, EARFCN 同桶) + 距离 + overlap
# ─────────────────────────────────────────────────────────────────
def _detect_co_channel(
    sectors: List[Dict[str, Any]],
    max_distance_km: float,
    overlap_threshold: float,
) -> List[Dict[str, Any]]:
    bucketed: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = defaultdict(list)
    for s in sectors:
        bucket = (s["rat"], s["band"], int(round(s["frequency"])))
        bucketed[bucket].append(s)

    issues: List[Dict[str, Any]] = []
    cell_deg = min(0.02, max_distance_km / KM_PER_DEG * 2.0)
    for bucket in bucketed.values():
        if len(bucket) < 2:
            continue
        grid = _build_grid_index(bucket, cell_deg)
        for (gx, gy), cell_list in grid.items():
            neighbors: List[Dict[str, Any]] = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = grid.get((gx + dx, gy + dy))
                    if nb:
                        neighbors.extend(nb)
            for s1 in cell_list:
                for s2 in neighbors:
                    if s1 is s2:
                        continue
                    if id(s1) >= id(s2):
                        continue
                    if abs(s1["frequency"] - s2["frequency"]) > 0.1:
                        continue
                    d_ub = _quick_dist_km_ub(s1["point"], s2["point"])
                    if d_ub > max_distance_km:
                        continue
                    d_km = _distance_km_point(s1["point"], s2["point"])
                    if d_km > max_distance_km:
                        continue
                    bearing = _bearing(s1["point"], s2["point"])
                    rev_bearing = _bearing(s2["point"], s1["point"])
                    ov1 = _calculate_beam_overlap(s1["azimuth"], s1["beamwidth"], bearing)
                    ov2 = _calculate_beam_overlap(s2["azimuth"], s2["beamwidth"], rev_bearing)
                    if ov1 > overlap_threshold or ov2 > overlap_threshold:
                        severity = "High" if (ov1 > 60 and ov2 > 60) else "Medium"
                        issues.append({
                            "type": "Co-Channel",
                            "sector1": s1,
                            "sector2": s2,
                            "distance_km": round(d_km, 3),
                            "overlap1": round(ov1, 2),
                            "overlap2": round(ov2, 2),
                            "severity": severity,
                            "details": f"同频 {s1['frequency']:.1f}MHz 距离 {d_km:.2f}km",
                        })
                        _add_inbox(s1, s2, "Co-Channel", severity, d_km, max(ov1, ov2), max_distance_km)
                        _add_inbox(s2, s1, "Co-Channel", severity, d_km, max(ov1, ov2), max_distance_km)
    return issues


# ─────────────────────────────────────────────────────────────────
# 邻频: 同 (rat, freq_band) + Δf 5~20 + 距离/2 + overlap
# ─────────────────────────────────────────────────────────────────
def _detect_adjacent_channel(
    sectors: List[Dict[str, Any]],
    max_distance_km: float,
    overlap_threshold: float,
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    half_dist = max_distance_km * 0.5
    cell_deg = min(0.02, half_dist / KM_PER_DEG * 2.0)

    # 同 (rat, freq_band) 内, 把 ERACFN 相邻 ±20 内所有桶合并做网格
    buckets: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = defaultdict(list)
    for s in sectors:
        buckets[(s["rat"], s["band"], int(round(s["frequency"])))].append(s)

    grouped: Dict[Tuple[str, str], List[List[Dict[str, Any]]]] = defaultdict(list)
    for (rat, band, f_int), bucket in buckets.items():
        grouped[(rat, band)].append((f_int, bucket))

    for (rat, band), groups in grouped.items():
        f_int_set = {f for f, _ in groups}
        for f_int, bucket in groups:
            if len(bucket) < 1:
                continue
            # 邻频合并: 把 f_int ± [5..20] 的桶合进来
            neighbor_freqs = {f_int + d for d in range(5, 21)} & f_int_set
            merged: List[Dict[str, Any]] = list(bucket)
            seen: set = {id(x) for x in bucket}
            for nf in neighbor_freqs:
                for x in buckets[(rat, band, nf)]:
                    if id(x) in seen:
                        continue
                    seen.add(id(x))
                    merged.append(x)
            if len(merged) < 2:
                continue
            grid = _build_grid_index(merged, cell_deg)
            for (gx, gy), cell_list in grid.items():
                neighbors: List[Dict[str, Any]] = []
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nb = grid.get((gx + dx, gy + dy))
                        if nb:
                            neighbors.extend(nb)
                for s1 in cell_list:
                    for s2 in neighbors:
                        if s1 is s2:
                            continue
                        if id(s1) >= id(s2):
                            continue
                        freq_diff = abs(s1["frequency"] - s2["frequency"])
                        if freq_diff < 5 or freq_diff > 20:
                            continue
                        # 仅在同 (rat, freq_band) 内, EARFCN 不同桶即可
                        if abs(int(round(s1["frequency"])) - int(round(s2["frequency"]))) < 5:
                            continue
                        if abs(int(round(s1["frequency"])) - int(round(s2["frequency"]))) > 20:
                            continue
                        d_ub = _quick_dist_km_ub(s1["point"], s2["point"])
                        if d_ub > half_dist:
                            continue
                        d_km = _distance_km_point(s1["point"], s2["point"])
                        if d_km > half_dist:
                            continue
                        bearing = _bearing(s1["point"], s2["point"])
                        rev_bearing = _bearing(s2["point"], s1["point"])
                        ov1 = _calculate_beam_overlap(s1["azimuth"], s1["beamwidth"], bearing)
                        ov2 = _calculate_beam_overlap(s2["azimuth"], s2["beamwidth"], rev_bearing)
                        if ov1 > overlap_threshold or ov2 > overlap_threshold:
                            severity = "Medium" if d_km < 0.5 else "Low"
                            issues.append({
                                "type": "Adjacent Channel",
                                "sector1": s1,
                                "sector2": s2,
                                "distance_km": round(d_km, 3),
                                "freq_diff": round(freq_diff, 2),
                                "overlap1": round(ov1, 2),
                                "overlap2": round(ov2, 2),
                                "severity": severity,
                                "details": f"Δf={freq_diff:.1f}MHz 距离 {d_km:.2f}km",
                            })
                            _add_inbox(s1, s2, "Adjacent Channel", severity, d_km, max(ov1, ov2), max_distance_km)
                            _add_inbox(s2, s1, "Adjacent Channel", severity, d_km, max(ov1, ov2), max_distance_km)
    return issues


# ─────────────────────────────────────────────────────────────────
# PCI 冲突: (rat, freq_band, EARFCN 同桶) 内 collision / mod3 / mod6
# 同时: 记下同桶同 PCI 的最近距离
# ─────────────────────────────────────────────────────────────────
def _detect_pci_conflicts(
    sectors: List[Dict[str, Any]],
    max_distance_km: float,
    overlap_threshold: float,
    detect_collision: bool = True,
    detect_mod3: bool = True,
    detect_mod6: bool = False,
) -> List[Dict[str, Any]]:
    if not any([detect_collision, detect_mod3, detect_mod6]):
        return []
    issues: List[Dict[str, Any]] = []

    # 仅 PCI 有效
    valid = [s for s in sectors if s["pci"] >= 0]
    if not valid:
        return issues

    buckets: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = defaultdict(list)
    for s in valid:
        buckets[(s["rat"], s["band"], int(round(s["frequency"])))].append(s)

    cell_deg = min(0.02, max_distance_km / KM_PER_DEG * 2.0)

    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        # 1) 同桶内为每个 cell 找 同 PCI 的最近距离
        grid_all = _build_grid_index(bucket, cell_deg * 1.5)  # 同 PCI 不必受 max_distance 限制, 略放宽
        # 较慢但只在桶内(<几十), 用桶内全配对 O(n²) 即可
        _compute_same_pci_min_dist(bucket, max_distance_km, grid_all)
        grid = _build_grid_index(bucket, cell_deg)
        for (gx, gy), cell_list in grid.items():
            neighbors: List[Dict[str, Any]] = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = grid.get((gx + dx, gy + dy))
                    if nb:
                        neighbors.extend(nb)
            for s1 in cell_list:
                for s2 in neighbors:
                    if s1 is s2:
                        continue
                    if id(s1) >= id(s2):
                        continue
                    same_pci = (s1["pci"] == s2["pci"])
                    pci1_mod3, pci2_mod3 = s1["pci"] % 3, s2["pci"] % 3
                    pci1_mod6, pci2_mod6 = s1["pci"] % 6, s2["pci"] % 6
                    has_conflict = (
                        (detect_collision and same_pci) or
                        (detect_mod3 and not same_pci and pci1_mod3 == pci2_mod3) or
                        (detect_mod6 and not same_pci and pci1_mod3 != pci2_mod3 and pci1_mod6 == pci2_mod6)
                    )
                    if not has_conflict:
                        continue
                    d_ub = _quick_dist_km_ub(s1["point"], s2["point"])
                    if d_ub > max_distance_km:
                        continue
                    d_km = _distance_km_point(s1["point"], s2["point"])
                    if d_km > max_distance_km:
                        continue
                    bearing = _bearing(s1["point"], s2["point"])
                    rev_bearing = _bearing(s2["point"], s1["point"])
                    ov1 = _calculate_beam_overlap(s1["azimuth"], s1["beamwidth"], bearing)
                    ov2 = _calculate_beam_overlap(s2["azimuth"], s2["beamwidth"], rev_bearing)
                    if ov1 > overlap_threshold or ov2 > overlap_threshold:
                        if same_pci:
                            conflict_type = "collision"
                            severity = "Critical"
                        elif pci1_mod3 == pci2_mod3:
                            conflict_type = "mod3"
                            severity = "High"
                        else:
                            conflict_type = "mod6"
                            severity = "Medium"
                        issues.append({
                            "type": "PCI Conflict",
                            "sector1": s1,
                            "sector2": s2,
                            "distance_km": round(d_km, 3),
                            "conflict_type": conflict_type,
                            "pci1": s1["pci"],
                            "pci2": s2["pci"],
                            "overlap1": round(ov1, 2),
                            "overlap2": round(ov2, 2),
                            "severity": severity,
                            "details": f"{conflict_type} ({s1['pci']} vs {s2['pci']}) 距离 {d_km:.2f}km",
                        })
                        _add_inbox(s1, s2, f"PCI {conflict_type}", severity, d_km, max(ov1, ov2), max_distance_km)
                        _add_inbox(s2, s1, f"PCI {conflict_type}", severity, d_km, max(ov1, ov2), max_distance_km)
    return issues


def _compute_same_pci_min_dist(
    bucket: List[Dict[str, Any]],
    max_distance_km: float,
    _grid_unused: Any = None,
) -> None:
    """桶内为每个 sector 找同 PCI 的最近距离 (写入 sector["same_pci_min_km"])"""
    by_pci: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for s in bucket:
        by_pci[s["pci"]].append(s)
    for _, group in by_pci.items():
        if len(group) < 2:
            continue
        # 同 PCI 桶若 >100 走网格, 否则 O(n²)
        if len(group) > 200:
            cell_deg = min(0.02, max_distance_km * 3 / KM_PER_DEG * 2)
            grid = _build_grid_index(group, cell_deg)
            for (gx, gy), cell_list in grid.items():
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nb = grid.get((gx + dx, gy + dy))
                        if nb:
                            cell_list.extend(nb)
            # 仍按 O(n²) 求最近
        n = len(group)
        for i in range(n):
            si = group[i]
            si_min = si["same_pci_min_km"]
            for j in range(i + 1, n):
                sj = group[j]
                d_km = _distance_km_point(si["point"], sj["point"])
                if si_min is None or d_km < si_min:
                    si["same_pci_min_km"] = d_km
                    si["same_pci_min_partner"] = sj["sector_id"]
                    si_min = d_km
                sj_min = sj["same_pci_min_km"]
                if sj_min is None or d_km < sj_min:
                    sj["same_pci_min_km"] = d_km
                    sj["same_pci_min_partner"] = si["sector_id"]


# ─────────────────────────────────────────────────────────────────
# 把 sectors 累加的 _weighted / _inbox 折算为 cell_scores
# ─────────────────────────────────────────────────────────────────
def _score_grade(raw: float) -> str:
    if raw >= 70:
        return "Critical"
    if raw >= 40:
        return "High"
    if raw >= 15:
        return "Medium"
    if raw > 0:
        return "Low"
    return "Clean"


def _assess_cell_scores(
    sectors: List[Dict[str, Any]],
    max_distance_km: float,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in sectors:
        inbox = s.get("_inbox", [])
        top = s.get("_top", [])[:3]
        raw = min(100.0, round(s["_weighted"] * 10.0, 1))
        out.append({
            "ecgi": s["sector_id"],
            "name": s["cell"].get("name"),
            "rat": s["rat"],
            "freq_band": s["band"],
            "earfcn": int(round(s["frequency"])) if s["frequency"] else None,
            "pci": s["pci"],
            "lat": s["point"][0],
            "lon": s["point"][1],
            "score": int(round(raw)),
            "grade": _score_grade(raw),
            "issues_count": len(inbox),
            "same_pci_min_km": (round(s["same_pci_min_km"], 3)
                                if s["same_pci_min_km"] is not None else None),
            "same_pci_min_partner": s.get("same_pci_min_partner"),
            "top_issues": top,
        })
    return out


# ─────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────
def analyze_interference(
    cells: List[Dict[str, Any]],
    interference_distance_km: float = 5.0,
    overlap_threshold: float = 30.0,
    detect_co_channel: bool = True,
    detect_adjacent_channel: bool = True,
    detect_pci_collision: bool = True,
    detect_mod3: bool = True,
    detect_mod6: bool = False,
    area: Optional[Dict[str, Any]] = None,
    rat_filter: Optional[str] = None,        # "LTE" / "NR" / None
    freq_band_filter: Optional[str] = None,  # e.g. "FDD1800" / None
) -> Dict[str, Any]:
    """
    主入口: 三检干扰分析, 分桶 (rat, freq_band[, EARFCN]).
    args:
      area:            可选, 圈选区域 (rect/circle)
      rat_filter:      只分析指定 RAT (LTE/NR), None = 全 RAT
      freq_band_filter: 只分析指定 freq_band, None = 全频段
    """
    from cell_filters import filter_cells_for_map_and_plan

    cells = filter_cells_for_map_and_plan(cells)
    sectors = _build_sectors(cells)

    # 区域/RAT/频段过滤 (在分桶前)
    if rat_filter or freq_band_filter:
        sectors = [s for s in sectors
                   if (not rat_filter or s["rat"] == rat_filter)
                   and (not freq_band_filter or s["band"] == freq_band_filter)]
    sectors = filter_sectors_by_area(sectors, area)
    if not sectors:
        return {
            "success": True,
            "issues": [],
            "stats": _stats([]),
            "mitigation": generate_mitigation_report([]),
            "truncated": False,
            "cell_scores": [],
            "params": {
                "interference_distance_km": interference_distance_km,
                "overlap_threshold": overlap_threshold,
                "detect_co_channel": detect_co_channel,
                "detect_adjacent_channel": detect_adjacent_channel,
                "detect_pci_collision": detect_pci_collision,
                "detect_mod3": detect_mod3,
                "detect_mod6": detect_mod6,
                "rat_filter": rat_filter,
                "freq_band_filter": freq_band_filter,
                "area": area,
            },
        }

    issues: List[Dict[str, Any]] = []
    if detect_co_channel:
        issues.extend(_detect_co_channel(sectors, interference_distance_km, overlap_threshold))
    if detect_adjacent_channel:
        issues.extend(_detect_adjacent_channel(sectors, interference_distance_km, overlap_threshold))
    if detect_pci_collision or detect_mod3 or detect_mod6:
        issues.extend(_detect_pci_conflicts(
            sectors, interference_distance_km, overlap_threshold,
            detect_pci_collision, detect_mod3, detect_mod6,
        ))

    # 全量 stats
    stats = _stats(issues)

    # 严重度裁剪 (返回列表)
    MAX_ISSUES_RETURNED = 10000
    total_count = len(issues)
    truncated = total_count > MAX_ISSUES_RETURNED
    if truncated:
        severity_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        issues_sorted = sorted(
            issues,
            key=lambda it: (severity_rank.get(it["severity"], 99), it["distance_km"]),
        )
        issues = issues_sorted[:MAX_ISSUES_RETURNED]
        stats = dict(stats)
        stats["returned"] = MAX_ISSUES_RETURNED
        stats["truncated"] = True

    # 序列化 (sector -> 简化 dict)
    simple_issues = []
    for it in issues:
        s1 = it["sector1"]
        s2 = it["sector2"]
        si = {
            "type": it["type"],
            "sector1": {
                "ecgi": s1["sector_id"],
                "name": s1["cell"].get("name"),
                "lat": s1["point"][0],
                "lon": s1["point"][1],
                "rat": s1["rat"],
                "band": s1["band"],
                "pci": s1["pci"],
                "azimuth": s1["azimuth"],
                "beamwidth": s1["beamwidth"],
            },
            "sector2": {
                "ecgi": s2["sector_id"],
                "name": s2["cell"].get("name"),
                "lat": s2["point"][0],
                "lon": s2["point"][1],
                "rat": s2["rat"],
                "band": s2["band"],
                "pci": s2["pci"],
                "azimuth": s2["azimuth"],
                "beamwidth": s2["beamwidth"],
            },
            "distance_km": it["distance_km"],
            "overlap1": it["overlap1"],
            "overlap2": it["overlap2"],
            "severity": it["severity"],
            "details": it["details"],
        }
        if it["type"] == "Adjacent Channel":
            si["freq_diff"] = it.get("freq_diff", 0)
        if it["type"] == "PCI Conflict":
            si["conflict_type"] = it["conflict_type"]
        simple_issues.append(si)

    mitigation = generate_mitigation_report(simple_issues)
    if isinstance(mitigation, list):
        mitigation = "\n".join(mitigation)
    cell_scores = _assess_cell_scores(sectors, interference_distance_km)

    return {
        "success": True,
        "issues": simple_issues,
        "stats": stats,
        "mitigation": mitigation,
        "truncated": truncated,
        "cell_scores": cell_scores,
        "params": {
            "interference_distance_km": interference_distance_km,
            "overlap_threshold": overlap_threshold,
            "detect_co_channel": detect_co_channel,
            "detect_adjacent_channel": detect_adjacent_channel,
            "detect_pci_collision": detect_pci_collision,
            "detect_mod3": detect_mod3,
            "detect_mod6": detect_mod6,
            "rat_filter": rat_filter,
            "freq_band_filter": freq_band_filter,
            "area": area,
        },
    }


# ─────────────────────────────────────────────────────────────────
# stats / mitigation
# ─────────────────────────────────────────────────────────────────
def _stats(issues: List[Dict[str, Any]]) -> Dict[str, int]:
    s = {
        "total": len(issues),
        "co_channel": 0,
        "adjacent_channel": 0,
        "pci_collision": 0,
        "pci_mod3": 0,
        "pci_mod6": 0,
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }
    for it in issues:
        t = it["type"]
        if t == "Co-Channel":
            s["co_channel"] += 1
        elif t == "Adjacent Channel":
            s["adjacent_channel"] += 1
        elif t == "PCI Conflict":
            ct = it["conflict_type"]
            if ct == "collision":
                s["pci_collision"] += 1
            elif ct == "mod3":
                s["pci_mod3"] += 1
            else:
                s["pci_mod6"] += 1
        sev = it.get("severity")
        if sev == "Critical":
            s["critical"] += 1
        elif sev == "High":
            s["high"] += 1
        elif sev == "Medium":
            s["medium"] += 1
        elif sev == "Low":
            s["low"] += 1
    return s


def generate_mitigation_report(issues: List[Dict[str, Any]]) -> List[str]:
    """给出可读的处置建议"""
    if not issues:
        return ["✔ 所有勾选检测项均无严重干扰, 暂无需处置。"]
    suggestions: List[str] = []
    # collision
    n_collision = sum(1 for it in issues if it["type"] == "PCI Conflict" and it.get("conflict_type") == "collision")
    n_mod3 = sum(1 for it in issues if it["type"] == "PCI Conflict" and it.get("conflict_type") == "mod3")
    n_mod6 = sum(1 for it in issues if it["type"] == "PCI Conflict" and it.get("conflict_type") == "mod6")
    n_co = sum(1 for it in issues if it["type"] == "Co-Channel")
    n_adj = sum(1 for it in issues if it["type"] == "Adjacent Channel")
    if n_collision:
        suggestions.append(f"!! 检出 {n_collision} 处 PCI collision(完全同 PCI), 必须重新规划 PCI。")
    if n_mod3:
        suggestions.append(f"!  检出 {n_mod3} 处 mod3 冲突, 建议调整 PCI 错开 mod 3 取值。")
    if n_mod6:
        suggestions.append(f"   检出 {n_mod6} 处 mod6 冲突, 优先级低于 mod3, 可在弱信号区域保守调。")
    if n_co:
        suggestions.append(f"*  检出 {n_co} 处同频干扰, 可降低同频扇区功率 / 调整方位 / 减小干扰距离参数。")
    if n_adj:
        suggestions.append(f"   检出 {n_adj} 处邻频干扰, 通常影响有限, 可结合 PRB 错开规划。")
    return suggestions
