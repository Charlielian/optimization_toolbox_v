"""
规划后 PCI 质量说明：得分、最近邻 PCI、干扰贡献、候选对比。
供 API / 前端展示，与 PciEvaluator 口径一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from cell_filters import filter_cells_for_map_and_plan, is_nb_znh_cell
from conflict_check import check_pair
from geo_utils import mutual_back_facing, vincenty_distance
from pci_evaluator import PciEvaluator
from pci_scope import cell_freq_band_key, cells_same_freq_band_for_pci

# 导出「PCI干扰明细」：该半径(km)内全部 PCI 层干扰邻区（不限前 N 条）
DEFAULT_EXPORT_INTERFERENCE_RADIUS_KM = 5.0


def _pci_field_value(cell: Dict[str, Any], use_new: bool) -> Optional[int]:
    key = "new_pci" if use_new else "pci"
    v = cell.get(key)
    if v is None and use_new:
        v = cell.get("pci")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _distance_km(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return vincenty_distance(a["lat"], a["lon"], b["lat"], b["lon"]) / 1000.0


def _freq_band_export_label(cell: Optional[Dict[str, Any]]) -> str:
    """导出用频段显示（标准化键 + 工参原文兜底）。"""
    if not cell:
        return ""
    key = cell_freq_band_key(cell)
    if key and key != "UNKNOWN":
        return key
    for fld in ("freq_band", "plan_freq_band", "freq_band_raw", "freq_band_label"):
        v = cell.get(fld)
        if v is not None and str(v).strip() and str(v).strip() not in ("默认", "—", "-"):
            return str(v).strip()
    return key if key else ""


def _relation_zh(pci_a: int, pci_b: int, rat: str) -> str:
    if pci_a == pci_b:
        return "同PCI"
    if pci_a % 3 == pci_b % 3:
        if rat == "NR" and pci_a % 30 == pci_b % 30:
            return "Mod30(含Mod3)"
        return "Mod3"
    if rat == "NR" and pci_a % 30 == pci_b % 30:
        return "Mod30"
    return "无PCI层冲突"


def _quality_label(score: float, has_hard: bool, in_collect_conflict: bool) -> str:
    if has_hard or in_collect_conflict:
        return "需关注"
    if score <= 0.05:
        return "优"
    if score <= 0.35:
        return "良"
    if score <= 0.85:
        return "一般"
    return "偏差"


def _build_neighbors_for_cell(
    target: Dict[str, Any],
    all_cells: List[Dict[str, Any]],
    evalr: PciEvaluator,
    *,
    exclude_ecgi: Optional[str] = None,
    same_rat_only: bool = True,
) -> List[Tuple[int, Dict[str, Any], float]]:
    max_r = max(2.0 * evalr.safe_dist_km, evalr.same_pci_min_km)
    rat = target.get("rat", "LTE")
    out: List[Tuple[int, Dict[str, Any], float]] = []
    for other in all_cells:
        if other.get("ecgi") == exclude_ecgi or other.get("ecgi") == target.get("ecgi"):
            continue
        if is_nb_znh_cell(other):
            continue
        if same_rat_only and not cells_same_freq_band_for_pci(target, other):
            continue
        op = _pci_field_value(other, use_new=True)
        if op is None:
            continue
        try:
            d = _distance_km(target, other)
        except Exception:
            continue
        if d > max_r:
            continue
        out.append((op, other, d))
    return out


def _neighbor_contributions(
    target_pci: int,
    target_cell: Dict[str, Any],
    neighbors: Sequence[Tuple[int, Dict[str, Any], float]],
    evalr: PciEvaluator,
) -> List[Dict[str, Any]]:
    rat = target_cell.get("rat", "LTE")
    rows: List[Dict[str, Any]] = []
    for nbr_pci, nbr_cell, d_km in neighbors:
        back_factor = 1.0
        back_facing = False
        if evalr.directional_filter and d_km is not None and d_km >= evalr.safe_dist_km:
            if mutual_back_facing(target_cell, nbr_cell):
                back_factor = 0.5
                back_facing = True
        pen = evalr.neighbor_penalty(target_pci, nbr_pci, d_km, back_factor)
        if pen <= 1e-9:
            continue
        conf, _ = check_pair(target_pci, nbr_pci, rat)
        rows.append({
            "ecgi": nbr_cell.get("ecgi"),
            "name": nbr_cell.get("name"),
            "pci": nbr_pci,
            "distance_km": round(d_km, 3),
            "relation": _relation_zh(target_pci, nbr_pci, rat),
            "penalty": round(pen, 4),
            "back_facing": back_facing,
            "pci_pair_conflict": conf,
        })
    rows.sort(key=lambda x: (-x["penalty"], x["distance_km"]))
    return rows


def _interference_within_radius_km(
    target_pci: int,
    target_cell: Dict[str, Any],
    all_cells: List[Dict[str, Any]],
    evalr: PciEvaluator,
    radius_km: float,
) -> List[Dict[str, Any]]:
    """
    半径内同制式全部邻区 PCI 关系（用于导出明细）。
    含惩罚贡献>0 的干扰项，以及同 PCI/Mod3/Mod30 关系但惩罚为 0 的项。
    """
    rat = target_cell.get("rat", "LTE")
    rows: List[Dict[str, Any]] = []
    for other in all_cells:
        if other.get("ecgi") == target_cell.get("ecgi") or is_nb_znh_cell(other):
            continue
        if not cells_same_freq_band_for_pci(target_cell, other):
            continue
        op = _pci_field_value(other, use_new=True)
        if op is None:
            continue
        try:
            d_km = _distance_km(target_cell, other)
        except Exception:
            continue
        if d_km > radius_km:
            continue
        back_factor = 1.0
        back_facing = False
        if evalr.directional_filter and d_km >= evalr.safe_dist_km:
            if mutual_back_facing(target_cell, other):
                back_factor = 0.5
                back_facing = True
        pen = evalr.neighbor_penalty(target_pci, op, d_km, back_factor)
        relation = _relation_zh(target_pci, op, rat)
        conf, _ = check_pair(target_pci, op, rat)
        if pen <= 1e-9 and relation == "无PCI层冲突":
            continue
        rows.append({
            "ecgi": other.get("ecgi"),
            "name": other.get("name"),
            "pci": op,
            "freq_band": _freq_band_export_label(other),
            "distance_km": round(d_km, 3),
            "relation": relation,
            "penalty": round(pen, 4),
            "back_facing": back_facing,
            "pci_pair_conflict": conf,
        })
    rows.sort(key=lambda x: (-x["penalty"], x["distance_km"]))
    return rows


def build_cell_pci_quality(
    cell: Dict[str, Any],
    all_cells: List[Dict[str, Any]],
    *,
    check_mod30: bool = True,
    directional_filter: bool = True,
    conflict_ecgis: Optional[Set[str]] = None,
    top_neighbors: int = 8,
    top_contributors: int = 5,
    export_interference_radius_km: float = DEFAULT_EXPORT_INTERFERENCE_RADIUS_KM,
) -> Optional[Dict[str, Any]]:
    pci = _pci_field_value(cell, use_new=True)
    if pci is None:
        return None

    evalr = PciEvaluator.from_cell(
        cell, check_mod30=check_mod30, directional_filter=directional_filter,
    )
    neighbors = _build_neighbors_for_cell(cell, all_cells, evalr)
    total_score = evalr.score_cell(pci, cell, neighbors)
    contribs = _neighbor_contributions(pci, cell, neighbors, evalr)
    hard = evalr.hard_violations(pci, cell, neighbors)

    # 最近同制式小区（任意 PCI）
    nearest_any: Optional[Dict[str, Any]] = None
    nearest_same_pci: Optional[Dict[str, Any]] = None
    nearest_mod3: Optional[Dict[str, Any]] = None
    rat = cell.get("rat", "LTE")

    for other in all_cells:
        if other.get("ecgi") == cell.get("ecgi") or is_nb_znh_cell(other):
            continue
        if not cells_same_freq_band_for_pci(cell, other):
            continue
        op = _pci_field_value(other, use_new=True)
        if op is None:
            continue
        try:
            d = _distance_km(cell, other)
        except Exception:
            continue
        item = {
            "ecgi": other.get("ecgi"),
            "name": other.get("name"),
            "pci": op,
            "distance_km": round(d, 3),
            "relation": _relation_zh(pci, op, rat),
        }
        if nearest_any is None or d < nearest_any["_d"]:
            nearest_any = {**item, "_d": d}
        if op == pci and (nearest_same_pci is None or d < nearest_same_pci["_d"]):
            nearest_same_pci = {**item, "_d": d}
        if pci % 3 == op % 3 and (nearest_mod3 is None or d < nearest_mod3["_d"]):
            nearest_mod3 = {**item, "_d": d}

    def _strip(d: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not d:
            return None
        return {k: v for k, v in d.items() if k != "_d"}

    ecgi = cell.get("ecgi")
    in_conflict = bool(
        conflict_ecgis and ecgi and ecgi in conflict_ecgis
    )

    candidates_report: List[Dict[str, Any]] = []
    raw_cands = cell.get("pci_candidates")
    if isinstance(raw_cands, list) and raw_cands:
        for cp in raw_cands[:10]:
            try:
                cpi = int(cp)
            except (TypeError, ValueError):
                continue
            cs = evalr.score_cell(cpi, cell, neighbors)
            candidates_report.append({
                "pci": cpi,
                "score": round(cs, 4),
                "is_chosen": cpi == pci,
            })

    return {
        "ecgi": ecgi,
        "pci": pci,
        "score": round(total_score, 4),
        "score_explain": (
            f"冲突评分={total_score:.3f}（越小越好；仅统计同制式同频段「{cell_freq_band_key(cell)}」邻区；"
            f"由同PCI/Mod3/Mod30 与距离加权，安全距离约 {evalr.safe_dist_km}km，"
            f"同PCI复用约 {evalr.same_pci_min_km}km）"
        ),
        "quality_label": _quality_label(total_score, bool(hard), in_conflict),
        "thresholds": {
            "safe_dist_km": evalr.safe_dist_km,
            "same_pci_min_km": evalr.same_pci_min_km,
        },
        "hard_violations": hard[:10],
        "in_conflict_list": in_conflict,
        "nearest_cell": _strip(nearest_any),
        "nearest_same_pci": _strip(nearest_same_pci),
        "nearest_mod3": _strip(nearest_mod3),
        "top_interference": contribs[:top_contributors],
        "interference_within_km": _interference_within_radius_km(
            pci, cell, all_cells, evalr, export_interference_radius_km,
        ),
        "interference_export_radius_km": export_interference_radius_km,
        "neighbor_count_in_radius": len(neighbors),
        "pci_candidates_scores": candidates_report,
    }


def build_pci_quality_report(
    cells: List[Dict[str, Any]],
    conflicts: Optional[List[Dict[str, Any]]] = None,
    *,
    ecgi_filter: Optional[Set[str]] = None,
    check_mod30: bool = True,
    directional_filter: bool = True,
    max_cells: int = 5000,
    export_interference_radius_km: float = DEFAULT_EXPORT_INTERFERENCE_RADIUS_KM,
) -> Dict[str, Any]:
    """
    为已规划小区生成质量报告。默认跳过无 new_pci 的条目。
    ecgi_filter: 仅报告这些小区（单站/局部）；None 表示全部（可截断 max_cells）。
    """
    pool = filter_cells_for_map_and_plan(cells)
    conflict_ecgis: Set[str] = set()
    if conflicts:
        for c in conflicts:
            for side in ("a", "b"):
                e = c.get(side, {}).get("ecgi")
                if e:
                    conflict_ecgis.add(e)

    targets = [
        c for c in pool
        if _pci_field_value(c, use_new=True) is not None
        and (ecgi_filter is None or c.get("ecgi") in ecgi_filter)
    ]
    truncated = False
    if ecgi_filter is None and len(targets) > max_cells:
        targets = targets[:max_cells]
        truncated = True

    per_cell: List[Dict[str, Any]] = []
    scores: List[float] = []
    label_counts: Dict[str, int] = {"优": 0, "良": 0, "一般": 0, "偏差": 0, "需关注": 0}

    for c in targets:
        q = build_cell_pci_quality(
            c, pool,
            check_mod30=check_mod30,
            directional_filter=directional_filter,
            conflict_ecgis=conflict_ecgis,
            export_interference_radius_km=export_interference_radius_km,
        )
        if not q:
            continue
        per_cell.append(q)
        scores.append(q["score"])
        label_counts[q["quality_label"]] = label_counts.get(q["quality_label"], 0) + 1

    avg = sum(scores) / len(scores) if scores else 0.0
    return {
        "summary": {
            "cells_reported": len(per_cell),
            "truncated": truncated,
            "avg_score": round(avg, 4),
            "max_score": round(max(scores), 4) if scores else 0.0,
            "quality_distribution": label_counts,
            "cells_in_conflict_list": len(conflict_ecgis),
        },
        "cells": per_cell,
    }


def attach_pci_quality_to_cells(
    cells: List[Dict[str, Any]],
    report: Dict[str, Any],
) -> None:
    """把简要质量字段写入小区 dict（供 /api/cells 与导出）。"""
    by_ecgi = {r["ecgi"]: r for r in report.get("cells", []) if r.get("ecgi")}
    for c in cells:
        e = c.get("ecgi")
        if e not in by_ecgi:
            continue
        q = by_ecgi[e]
        c["pci_quality"] = {
            "score": q["score"],
            "quality_label": q["quality_label"],
            "nearest_same_pci_km": (q.get("nearest_same_pci") or {}).get("distance_km"),
            "top_interference": q.get("top_interference", [])[:3],
        }


def pci_quality_export_columns(q: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """PCI规划表 / 工参导出用列（中文列名）。"""
    if not q:
        return {
            "PCI冲突评分": "",
            "PCI质量等级": "",
            "评分说明": "",
            "最近小区(km)": "",
            "最近小区名": "",
            "最近小区PCI": "",
            "最近小区关系": "",
            "最近同PCI(km)": "",
            "最近同PCI小区": "",
            "最近同PCI值": "",
            "最近Mod3(km)": "",
            "最近Mod3小区": "",
            "最近Mod3_PCI": "",
            "5km内干扰条数": "",
            "主要干扰说明": "",
            "硬约束告警": "",
            "在冲突清单": "",
            "候选PCI评分": "",
        }

    def _nbr(key: str, field: str) -> Any:
        n = q.get(key)
        if not n:
            return ""
        return n.get(field, "")

    within = q.get("interference_within_km")
    radius = q.get("interference_export_radius_km", DEFAULT_EXPORT_INTERFERENCE_RADIUS_KM)
    within_count = len(within) if within is not None else len(q.get("top_interference") or [])
    tops = q.get("top_interference") or (within or [])[:5]
    inter_lines = []
    for i, t in enumerate(tops[:5], 1):
        bf = "背向减半" if t.get("back_facing") else ""
        inter_lines.append(
            f"{i}){t.get('name') or t.get('ecgi')} PCI{t.get('pci')} "
            f"{t.get('distance_km')}km {t.get('relation')} 贡献{t.get('penalty')}{' '+bf if bf else ''}"
        )
    hard = q.get("hard_violations") or []
    cands = q.get("pci_candidates_scores") or []
    cand_str = " | ".join(
        f"{x.get('pci')}({x.get('score')}){'*' if x.get('is_chosen') else ''}" for x in cands[:8]
    )

    return {
        "PCI冲突评分": q.get("score", ""),
        "PCI质量等级": q.get("quality_label", ""),
        "评分说明": q.get("score_explain", ""),
        "最近小区(km)": _nbr("nearest_cell", "distance_km"),
        "最近小区名": _nbr("nearest_cell", "name"),
        "最近小区PCI": _nbr("nearest_cell", "pci"),
        "最近小区关系": _nbr("nearest_cell", "relation"),
        "最近同PCI(km)": _nbr("nearest_same_pci", "distance_km"),
        "最近同PCI小区": _nbr("nearest_same_pci", "name"),
        "最近同PCI值": _nbr("nearest_same_pci", "pci"),
        "最近Mod3(km)": _nbr("nearest_mod3", "distance_km"),
        "最近Mod3小区": _nbr("nearest_mod3", "name"),
        "最近Mod3_PCI": _nbr("nearest_mod3", "pci"),
        f"{int(radius) if radius == int(radius) else radius}km内干扰条数": within_count,
        "主要干扰说明": (
            ("\n".join(inter_lines) + (f"\n（完整 {within_count} 条见「PCI干扰明细」sheet）" if within_count > 5 else ""))
            if inter_lines
            else (f"（{radius}km 内共 {within_count} 条，见「PCI干扰明细」）" if within_count else "")
        ),
        "硬约束告警": "; ".join(hard[:5]),
        "在冲突清单": "是" if q.get("in_conflict_list") else "否",
        "候选PCI评分": cand_str,
    }


def pci_quality_interference_detail_rows(
    planned_cells: List[Dict[str, Any]],
    quality_by_ecgi: Dict[str, Dict[str, Any]],
    ecgi_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """PCI干扰明细 sheet：每扇区 × 半径内全部 PCI 干扰邻区一行（默认 5km，见 interference_within_km）。"""
    rows: List[Dict[str, Any]] = []
    for c in planned_cells:
        ecgi = c.get("ecgi")
        q = quality_by_ecgi.get(ecgi) if ecgi else None
        if not q:
            continue
        src_band = _freq_band_export_label(c)
        radius = q.get("interference_export_radius_km", DEFAULT_EXPORT_INTERFERENCE_RADIUS_KM)
        tops = q.get("interference_within_km")
        if tops is None:
            tops = q.get("top_interference") or []
        if not tops:
            rows.append({
                "统计半径(km)": radius,
                "源ECGI": ecgi,
                "源小区名": c.get("name"),
                "源频段": src_band,
                "源扇区": c.get("sector_index", ""),
                "源新PCI": c.get("new_pci"),
                "干扰排序": "",
                "干扰小区": f"（{radius}km 内无 PCI 层干扰记录）",
                "干扰频段": "",
                "干扰PCI": "",
                "距离(km)": "",
                "关系": "",
                "惩罚贡献": "",
                "背向减半": "",
            })
            continue
        for rank, t in enumerate(tops, 1):
            nbr_ecgi = t.get("ecgi")
            nbr_cell = (ecgi_index or {}).get(nbr_ecgi) if nbr_ecgi else None
            nbr_band = t.get("freq_band") or _freq_band_export_label(nbr_cell)
            rows.append({
                "统计半径(km)": radius,
                "源ECGI": ecgi,
                "源小区名": c.get("name"),
                "源频段": src_band,
                "源扇区": c.get("sector_index", ""),
                "源新PCI": c.get("new_pci"),
                "干扰排序": rank,
                "干扰小区": t.get("name") or t.get("ecgi"),
                "干扰频段": nbr_band,
                "干扰PCI": t.get("pci"),
                "距离(km)": t.get("distance_km"),
                "关系": t.get("relation"),
                "惩罚贡献": t.get("penalty"),
                "背向减半": "是" if t.get("back_facing") else "否",
            })
    return rows