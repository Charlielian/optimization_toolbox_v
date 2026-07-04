"""
邻区加权规划引擎
7:3 加权打分: 距离70% + 扇区交叠30%
支持多制式(4G↔5G 由 rat 判定; 同频/异频由 freq_band 判定)、双向补齐、冗余过滤
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from cell_filters import is_nb_znh_cell
from geo_utils import (
    angle_diff,
    bearing_angle,
    sector_overlap_area,
    sector_polygon,
    vincenty_distance,
)

DEFAULT_MAX_NEIGHBORS = 600
DEFAULT_MAX_DISTANCE_KM = 5.0
DEFAULT_BEAMWIDTH = 65.0
DEFAULT_WEIGHT_DISTANCE = 0.7
DEFAULT_WEIGHT_OVERLAP = 0.3
DEFAULT_MIN_SCORE = 0.10

# 无有效频段标签时不参与同频判定 (避免 None==None 误判同频)
_FREQ_BAND_UNKNOWN = frozenset({"", "默认", "未知", None})


def _norm_rat(cell: Dict[str, Any]) -> str:
    """4G/5G 制式归一: LTE 系 → LTE, NR 系 → NR"""
    r = (cell.get("rat") or "").strip().upper()
    if r in ("LTE", "4G", "FDD-LTE", "TDD-LTE", "EUTRA", "EUTRAN"):
        return "LTE"
    if r in ("NR", "5G", "5G NR", "GNB"):
        return "NR"
    return r or "?"


def _freq_band_key(cell: Dict[str, Any]) -> Optional[str]:
    """邻区同频比较用频段: 优先 freq_band, 其次 freq_band_label"""
    for k in ("freq_band", "freq_band_label"):
        v = cell.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s and s not in _FREQ_BAND_UNKNOWN:
            return s
    return None


def _is_same_freq(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """同频: 双方均有有效 freq_band 且一致 (不用 earfcn)"""
    fa, fb = _freq_band_key(a), _freq_band_key(b)
    if fa is None or fb is None:
        return False
    return fa == fb


def _is_cross_system(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """跨制式 (4G↔5G): 由 rat 归一化后比较"""
    return _norm_rat(a) != _norm_rat(b)


def _nbr_type_label(src: Dict[str, Any], dst: Dict[str, Any]) -> str:
    """邻区关系类型: 4G_4G / 4G_5G / 5G_4G / 5G_5G (基于 rat)"""
    src_4g = _norm_rat(src) == "LTE"
    dst_4g = _norm_rat(dst) == "LTE"
    if src_4g and dst_4g:
        return "4G_4G"
    if src_4g and not dst_4g:
        return "4G_5G"
    if not src_4g and dst_4g:
        return "5G_4G"
    return "5G_5G"


def _passes_pre_filter(src: Dict[str, Any], dst: Dict[str, Any], max_distance_km: float) -> bool:
    """预过滤: 距离超阈值直接跳过; 方位角夹角过大的跳过(无交叠可能)"""
    d_m = vincenty_distance(src["lat"], src["lon"], dst["lat"], dst["lon"])
    if d_m / 1000.0 > max_distance_km:
        return False

    # 方位角夹角: src到dst的方位角 与 src扇区中心方位角 差值
    src_az = float(src.get("azimuth", 0))
    dst_az = float(dst.get("azimuth", 0))
    src_bw = float(src.get("beamwidth", DEFAULT_BEAMWIDTH))
    dst_bw = float(dst.get("beamwidth", DEFAULT_BEAMWIDTH))

    bearing = bearing_angle(src["lat"], src["lon"], dst["lat"], dst["lon"])
    rev_bearing = bearing_angle(dst["lat"], dst["lon"], src["lat"], src["lon"])

    diff_src = angle_diff(bearing, src_az)
    diff_dst = angle_diff(rev_bearing, dst_az)

    # 放宽: 只要任一方在对方扇区波束内即通过(更符合实际侧瓣传播)
    # 仅当双方都偏离超过beamwidth+30度时才过滤
    slack = 30.0
    if diff_src > src_bw / 2 + slack and diff_dst > dst_bw / 2 + slack:
        return False

    return True


def calc_neighbor_score(distance_m: float, overlap_area_m2: float,
                        max_dist_m: float, max_overlap_m2: float,
                        w_dist: float = DEFAULT_WEIGHT_DISTANCE,
                        w_overlap: float = DEFAULT_WEIGHT_OVERLAP) -> float:
    """开发文档4.1: 距离70% + 扇区交叠30%"""
    dist_norm = 1 - min(distance_m / max_dist_m, 1.0)
    overlap_norm = min(overlap_area_m2 / max_overlap_m2, 1.0)
    total_score = dist_norm * w_dist + overlap_norm * w_overlap
    return round(total_score, 4)


def _build_sector_cached(cell: Dict[str, Any], radius_m: float):
    """缓存扇区多边形(避免重复计算)"""
    if "_poly" not in cell or cell.get("_poly_radius") != radius_m:
        poly = sector_polygon(
            cell["lon"], cell["lat"],
            float(cell.get("azimuth", 0)),
            float(cell.get("beamwidth", DEFAULT_BEAMWIDTH)),
            radius_m,
        )
        cell["_poly"] = poly
        cell["_poly_radius"] = radius_m
    return cell["_poly"]


def _max_overlap_reference(cells: List[Dict[str, Any]], radius_m: float) -> float:
    """全网最大单扇区面积作为归一化参考"""
    sample = cells[0]
    poly = _build_sector_cached(sample, radius_m)
    return max(poly.area, 1.0)


def _candidate_pairs(
    cells: List[Dict[str, Any]],
    max_distance_km: float,
    enable_cross_system: bool = True,
    # ── 局部模式: 仅对 src in target_ecgis 生成候选对 ──
    target_ecgis: Optional[List[str]] = None,
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """生成所有候选邻区对(预过滤后)"""
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for i, src in enumerate(cells):
        if is_nb_znh_cell(src):
            continue
        # 局部模式: 仅处理目标源小区
        if target_ecgis is not None and src.get("ecgi") not in target_ecgis:
            continue
        for j, dst in enumerate(cells):
            if i == j:
                continue
            if is_nb_znh_cell(dst):
                continue
            if not enable_cross_system and _is_cross_system(src, dst):
                continue
            if not _passes_pre_filter(src, dst, max_distance_km):
                continue
            pairs.append((src, dst))
    return pairs


def _passes_nbr_plan_type(src: Dict[str, Any], dst: Dict[str, Any],
                          nbr_plan_types: Optional[List[str]]) -> bool:
    """
    按制式组合过滤: 4G_4G / 4G_5G / 5G_4G / 5G_5G
    nbr_plan_types 为 None 或空 → 不过滤 (全部通过)
    """
    if not nbr_plan_types:
        return True
    pair = _nbr_type_label(src, dst)
    if _norm_rat(src) not in ("LTE", "NR") or _norm_rat(dst) not in ("LTE", "NR"):
        return False
    return pair in nbr_plan_types


def _beam_overlap_for_pair(src: Dict[str, Any], dst: Dict[str, Any]) -> float:
    """调用 RFTools 风格的扇区夹角判断, 返回 0-100 平均重叠"""
    try:
        from interference_analysis import _calculate_beam_overlap
        az_a = float(src.get("azimuth", 0))
        bw_a = float(src.get("beamwidth") or src.get("beam") or 65.0)
        az_b = float(dst.get("azimuth", 0))
        bw_b = float(dst.get("beamwidth") or dst.get("beam") or 65.0)
        # 1 → 2 bearing
        bearing = bearing_angle(src["lat"], src["lon"], dst["lat"], dst["lon"])
        rev = (bearing + 180) % 360
        o1 = _calculate_beam_overlap(az_a, bw_a, bearing)
        o2 = _calculate_beam_overlap(az_b, bw_b, rev)
        return (o1 + o2) / 2.0
    except Exception:
        return 0.0


def plan_neighbors(
    cells: List[Dict[str, Any]],
    max_neighbors: int = DEFAULT_MAX_NEIGHBORS,
    max_distance_km: float = DEFAULT_MAX_DISTANCE_KM,
    min_overlap_ratio: float = 0.0,
    enable_cross_system: bool = True,
    enable_bidirectional: bool = True,
    score_threshold: float = DEFAULT_MIN_SCORE,
    weight_distance: float = DEFAULT_WEIGHT_DISTANCE,
    weight_overlap: float = DEFAULT_WEIGHT_OVERLAP,
    nbr_plan_types: Optional[List[str]] = None,
    use_beam_overlap_score: bool = False,
    # ── 局部模式: 仅对 target_ecgis 中的源小区做邻区规划 ──
    target_ecgis: Optional[List[str]] = None,
    # ── 工程惯例: 第一圈邻区强制加入 (不因 score 过滤) ──
    # 距离 ≤ first_ring_km 的候选邻区 score 强制为 1.0, 确保通过任何阈值
    # 适用于单站/局部规划: 复用半径内所有小区都应作为邻区候选
    first_ring_km: Optional[float] = None,
) -> Dict[str, Any]:
    """
    主入口: 全网邻区规划
    返回 {neighbors: {src_ecgi: [{dst, score, distance_m, overlap_m2}, ...]}, log: []}

    nbr_plan_types: 邻区规划类型白名单, 例 ['4G_4G', '4G_5G']; None=不过滤
    use_beam_overlap_score: True 时使用 RFTools 扇区夹角打分(0-100)替代 Shapely 面积
    """
    log: List[str] = []
    local_mode = target_ecgis is not None
    log.append(f"[邻区规划] {'局部' if local_mode else '全网'}: {len(cells)}小区, 最大邻区数: {max_neighbors}, 最大距离: {max_distance_km}km")
    if nbr_plan_types:
        log.append(f"[邻区规划] 邻区规划类型过滤: {nbr_plan_types}")
    if use_beam_overlap_score:
        log.append(f"[邻区规划] 启用 RFTools 扇区夹角打分")

    # 清理旧邻区
    for c in cells:
        c["neighbors"] = []
        c.pop("_poly", None)
        c.pop("_poly_radius", None)

    # 参考归一化面积 (用覆盖半径, 仅与站点类型相关: 宏站700/室分100/微站200)
    # 注意: 扇区 polygon 形状(beamwidth) 来自 CASE 映射, 不影响覆盖半径
    radius_m_ref = max((c.get("coverage_radius", 700) for c in cells), default=700)
    max_overlap_ref = _max_overlap_reference(cells, radius_m_ref)
    max_dist_m = max_distance_km * 1000.0

    # 候选对 (局部模式下仅遍历 target_ecgis)
    pairs = _candidate_pairs(cells, max_distance_km, enable_cross_system,
                             target_ecgis=target_ecgis)
    log.append(f"[邻区规划] 候选对: {len(pairs)}")

    # 计算每个候选对的得分
    score_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for src, dst in pairs:
        # 邻区规划类型过滤
        if not _passes_nbr_plan_type(src, dst, nbr_plan_types):
            continue
        d_m = vincenty_distance(src["lat"], src["lon"], dst["lat"], dst["lon"])
        if d_m > max_dist_m:
            continue
        # 同站小区互不配
        if src.get("site_name") and src["site_name"] == dst.get("site_name"):
            continue

        # 计算打分因子
        if use_beam_overlap_score:
            beam_ov = _beam_overlap_for_pair(src, dst)
            if beam_ov < 30.0:  # RFTools 默认 overlap 阈值
                continue
            overlap_norm = beam_ov / 100.0
            score = (1 - min(d_m / max_dist_m, 1.0)) * weight_distance + overlap_norm * weight_overlap
            score = round(score, 4)
            overlap_m2 = 0.0
        else:
            # 交叠面积
            overlap = 0.0
            try:
                poly_src = _build_sector_cached(src, float(src.get("coverage_radius", radius_m_ref)))
                poly_dst = _build_sector_cached(dst, float(dst.get("coverage_radius", radius_m_ref)))
                overlap = sector_overlap_area(poly_src, poly_dst)
            except Exception:
                overlap = 0.0
            if overlap < min_overlap_ratio * max_overlap_ref:
                continue
            score = calc_neighbor_score(d_m, overlap, max_dist_m, max_overlap_ref,
                                        weight_distance, weight_overlap)
            overlap_m2 = overlap

        # ── 第一圈邻区豁免: 距离 ≤ first_ring_km 的候选强制通过 score 过滤 ──
        # 工程上 reuse 半径内所有小区都应作为邻区, 但保留真实 score (不强制为 1.0)
        # 这样 UI 显示的得分能反映真实相关性, 用户调整 score_threshold 时仍有梯度变化
        is_first_ring = first_ring_km is not None and d_m <= first_ring_km * 1000.0

        if not is_first_ring and score < score_threshold:
            continue

        nbr_type = _nbr_type_label(src, dst)

        score_map[(src["ecgi"], dst["ecgi"])] = {
            "src": src, "dst": dst,
            "distance_m": d_m, "overlap_m2": overlap_m2,
            "score": score,
            "same_freq": _is_same_freq(src, dst),
            "cross_system": _is_cross_system(src, dst),
            "nbr_type": nbr_type,
        }

    # 每个源小区按得分排序, 取TopN
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for (s, d), info in score_map.items():
        by_src.setdefault(s, []).append(info)

    # ecgi → nbr_type 映射 (供双向补齐)
    ecgi_to_nbr_type: Dict[Tuple[str, str], str] = {}
    for (s, d), info in score_map.items():
        ecgi_to_nbr_type[(s, d)] = info.get("nbr_type", "4G_4G")

    neighbors_map: Dict[str, List[Dict[str, Any]]] = {c["ecgi"]: [] for c in cells}
    for src_ecgi, lst in by_src.items():
        # 同频优先, 然后按得分降序
        lst.sort(key=lambda x: ((0 if x["same_freq"] else 1), -x["score"]))
        top = lst[:max_neighbors]
        for item in top:
            neighbors_map[src_ecgi].append({
                "dst_ecgi": item["dst"]["ecgi"],
                "dst_name": item["dst"]["name"],
                "distance_m": round(item["distance_m"], 1),
                "overlap_m2": round(item["overlap_m2"], 1),
                "score": item["score"],
                "same_freq": item["same_freq"],
                "cross_system": item["cross_system"],
                "nbr_type": item.get("nbr_type", "4G_4G"),
            })

    # 双向补齐
    if enable_bidirectional:
        added = 0
        existing_pairs = set()
        for s, lst in neighbors_map.items():
            for item in lst:
                existing_pairs.add((s, item["dst_ecgi"]))

        ecgi_to_cell = {c["ecgi"]: c for c in cells}
        for src_ecgi, lst in list(neighbors_map.items()):
            for item in lst:
                reverse_pair = (item["dst_ecgi"], src_ecgi)
                if reverse_pair in existing_pairs:
                    continue
                # 双向补齐: 加入反向
                dst_cell = ecgi_to_cell.get(item["dst_ecgi"])
                src_cell = ecgi_to_cell.get(src_ecgi)
                if not dst_cell or not src_cell:
                    continue
                # 容量限制
                if len(neighbors_map[dst_cell["ecgi"]]) >= max_neighbors:
                    continue
                # 双向补齐: 类型取反
                src_t = item.get("nbr_type", "4G_4G")
                if src_t == "4G_4G":
                    rev_t = "4G_4G"
                elif src_t == "4G_5G":
                    rev_t = "5G_4G"
                elif src_t == "5G_4G":
                    rev_t = "4G_5G"
                else:
                    rev_t = "5G_5G"
                neighbors_map[dst_cell["ecgi"]].append({
                    "dst_ecgi": src_cell["ecgi"],
                    "dst_name": src_cell["name"],
                    "distance_m": item["distance_m"],
                    "overlap_m2": item["overlap_m2"],
                    "score": item["score"],
                    "same_freq": item["same_freq"],
                    "cross_system": item["cross_system"],
                    "auto_added": True,
                    "nbr_type": rev_t,
                })
                existing_pairs.add(reverse_pair)
                added += 1
        log.append(f"[邻区规划] 双向补齐: {added}条")

    # 写回到cells
    for c in cells:
        c["neighbors"] = neighbors_map.get(c["ecgi"], [])

    total = sum(len(v) for v in neighbors_map.values())
    avg = total / max(1, len(cells))
    log.append(f"[邻区规划] 总邻区关系: {total}条, 平均/小区: {avg:.1f}")

    return {
        "neighbors": neighbors_map,
        "log": log,
        "stats": {
            "total_cells": len(cells),
            "total_pairs": len(pairs),
            "scored_pairs": len(score_map),
            "neighbor_relations": total,
            "avg_neighbors": avg,
        }
    }


def detect_redundancy(cells: List[Dict[str, Any]],
                      max_distance_km: float = DEFAULT_MAX_DISTANCE_KM,
                      min_score: float = DEFAULT_MIN_SCORE) -> Dict[str, Any]:
    """
    邻区冗余/漏配检测:
    - 冗余: score < min_score 或 距离 > max_distance_km
    - 漏配: 距离近(<1km)但未配置为邻区
    """
    redundant: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    ecgi_to_cell = {c["ecgi"]: c for c in cells}

    # 检查每个邻区关系
    for c in cells:
        for n in c.get("neighbors", []):
            if n["distance_m"] / 1000.0 > max_distance_km:
                redundant.append({
                    "src": c["name"], "dst": n["dst_name"],
                    "reason": f"距离过远({n['distance_m']/1000:.2f}km)",
                    "distance_m": n["distance_m"],
                    "score": n["score"],
                })
            elif n["score"] < min_score:
                redundant.append({
                    "src": c["name"], "dst": n["dst_name"],
                    "reason": f"得分过低({n['score']})",
                    "distance_m": n["distance_m"],
                    "score": n["score"],
                })

    # 漏配检测: 距离<1km但未互为邻区
    for i, src in enumerate(cells):
        for j, dst in enumerate(cells):
            if i >= j:
                continue
            if src.get("site_name") == dst.get("site_name"):
                continue
            d_m = vincenty_distance(src["lat"], src["lon"], dst["lat"], dst["lon"])
            if d_m > 1000.0:
                continue
            # 检查是否互为邻区
            a_neighbors = {n["dst_ecgi"] for n in src.get("neighbors", [])}
            b_neighbors = {n["dst_ecgi"] for n in dst.get("neighbors", [])}
            if dst["ecgi"] not in a_neighbors or src["ecgi"] not in b_neighbors:
                missing.append({
                    "a": src["name"], "b": dst["name"],
                    "distance_m": d_m,
                    "reason": "近距离(<1km)未配置邻区"
                })

    return {
        "redundant": redundant,
        "missing": missing,
        "stats": {
            "redundant_count": len(redundant),
            "missing_count": len(missing),
        }
    }