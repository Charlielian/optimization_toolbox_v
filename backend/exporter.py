"""
导出模块
- 工参表(原字段 + 新PCI + 邻区清单)
- 冲突报表
- 邻区清单
- 华为/中兴 MML脚本
"""
from __future__ import annotations

import io
import json
from typing import Any, Dict, List, Optional, Set

import pandas as pd

from conflict_check import collect_conflicts
from pci_quality_report import (
    build_pci_quality_report,
    pci_quality_export_columns,
    pci_quality_interference_detail_rows,
)

# 华为LTE邻区关系MML模板
HUAWEI_LTE_MML_TEMPLATE = """ADD NRCELLEUTRANCELLRELATIONSHIP: NRCellId={cell_id}, EUTRANCellId={nbr_id}, isRemoveNoHOAllowed=0, RemoveAllowed=0, ncellIndividualOffset=dB0;
"""

# 华为NR邻区关系MML模板
HUAWEI_NR_MML_TEMPLATE = """ADD NRCELLRELATIONSHIP: NRCellId={cell_id}, NRCellId_neighbour={nbr_id}, isRemoveNoHOAllowed=0, RemoveAllowed=0, ncellIndividualOffset=dB0;
"""

# 中兴LTE邻区关系MML模板
ZTE_LTE_MML_TEMPLATE = """ADD NRCELLEUTRANCELL: NRCellId={cell_id}, EUTRANCellId={nbr_id}, RemoveAllowed=0, ncellIndividualOffset=0;
"""

# 中兴NR邻区关系MML模板
ZTE_NR_MML_TEMPLATE = """ADD NRCELLRELATION: NRCellId={cell_id}, NRCellId_neighbour={nbr_id}, RemoveAllowed=0, ncellIndividualOffset=0;
"""


def _pci_value(cell: Optional[Dict[str, Any]]) -> Any:
    if not cell:
        return ""
    p = cell.get("new_pci")
    if p is None:
        p = cell.get("pci")
    if p is None or (isinstance(p, int) and p < 0):
        return ""
    return p


def _ecgi_index(cells: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {c["ecgi"]: c for c in cells if c.get("ecgi")}


def _freq_band_label(cell: Optional[Dict[str, Any]]) -> str:
    """工参/现网小区频段（目标侧等）"""
    if not cell:
        return ""
    return cell.get("freq_band") or cell.get("freq_band_label") or ""


def _plan_src_freq_band(cell: Optional[Dict[str, Any]]) -> str:
    """源小区频段：单站页面 / 批量文件「规划频段」或「详细使用频段」"""
    if not cell:
        return ""
    pf = cell.get("plan_freq_band")
    if pf is not None and str(pf).strip():
        return str(pf).strip()
    raw = cell.get("freq_band_raw")
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return _freq_band_label(cell)


def _neighbor_relation_row(
    src: Dict[str, Any],
    n: Dict[str, Any],
    dst: Optional[Dict[str, Any]],
    *,
    key_style: str = "split",
) -> Dict[str, Any]:
    """
    邻区关系导出行 (长表).
    key_style: 'split' = 单站分 sheet 导出列名; 'full' = 全网邻区清单列名
    """
    if key_style == "full":
        base = {
            "源小区ECGI": src.get("ecgi"),
            "源小区名称": src.get("name"),
            "源制式": src.get("rat"),
            "源频段": _plan_src_freq_band(src),
            "源PCI": _pci_value(src),
            "源频点": src.get("earfcn"),
            "源TAC": src.get("tac"),
            "目标小区ECGI": n["dst_ecgi"],
            "目标小区名称": n["dst_name"],
        }
    else:
        base = {
            "源ECGI": src.get("ecgi"),
            "源小区名": src.get("name"),
            "源制式": src.get("rat"),
            "源频段": _plan_src_freq_band(src),
            "源PCI": _pci_value(src),
            "源频点": src.get("earfcn"),
            "源TAC": src.get("tac"),
            "目标ECGI": n["dst_ecgi"],
            "目标小区名": n["dst_name"],
        }
    dst_extra = {
        "目标厂家": (dst or {}).get("manufacturer", ""),
        "目标频段指示": (dst or {}).get("freq_band_ind", ""),
        "目标频段": (dst or {}).get("freq_band") or (dst or {}).get("freq_band_label", ""),
        "目标归属网管": (dst or {}).get("oms_name", ""),
        "目标PCI": _pci_value(dst),
        "目标频点": (dst or {}).get("earfcn", ""),
        "目标TAC": (dst or {}).get("tac", ""),
    }
    metrics = {
        "距离(m)": n["distance_m"],
        "交叠面积(m²)": n["overlap_m2"],
        "综合得分": n["score"],
        "同频": "是" if n.get("same_freq") else "否",
        "异系统": "是" if n.get("cross_system") else "否",
        "自动补齐": "是" if n.get("auto_added") else "否",
    }
    return {**base, **dst_extra, **metrics}


def _quality_by_ecgi_for_export(
    cells: List[Dict[str, Any]],
    ecgi_filter: Optional[Set[str]] = None,
    *,
    check_mod30: bool = True,
    directional_filter: bool = True,
    export_interference_radius_km: float = 5.0,
) -> Dict[str, Dict[str, Any]]:
    """导出前生成/合并 PCI 质量（优先 cell 上已有完整 pci_quality）。"""
    conflicts = collect_conflicts(cells, use_original_pci=False, directional_filter=directional_filter)
    report = build_pci_quality_report(
        cells,
        conflicts,
        ecgi_filter=ecgi_filter,
        check_mod30=check_mod30,
        directional_filter=directional_filter,
        max_cells=50000 if ecgi_filter else 5000,
        export_interference_radius_km=export_interference_radius_km,
    )
    by_ecgi = {r["ecgi"]: r for r in report.get("cells", []) if r.get("ecgi")}
    for c in cells:
        e = c.get("ecgi")
        if not e:
            continue
        pq = c.get("pci_quality")
        if isinstance(pq, dict) and pq.get("score_explain"):
            by_ecgi[e] = pq
        elif isinstance(pq, dict) and e not in by_ecgi:
            by_ecgi[e] = pq
    return by_ecgi


def export_workparams(
    cells: List[Dict[str, Any]],
    *,
    check_mod30: bool = True,
    directional_filter: bool = True,
) -> bytes:
    """
    导出规划后工参表
    字段: 原字段 + 新PCI + PCI质量说明 + 邻区数量 + 邻区ECGI列表
    """
    quality_by = _quality_by_ecgi_for_export(
        cells, None, check_mod30=check_mod30, directional_filter=directional_filter,
    )
    rows = []
    for c in cells:
        row = {
            "ECGI": c.get("ecgi"),
            "小区名称": c.get("name"),
            "站点名称": c.get("site_name") or c.get("name", "").rsplit("-", 1)[0],
            "制式": c.get("rat"),
            "频点": c.get("earfcn"),
            "经度": c.get("lon"),
            "纬度": c.get("lat"),
            "方位角": c.get("azimuth"),
            "覆盖半径(m)": c.get("radius"),
            "TAC": c.get("tac"),
            "原PCI": c.get("pci"),
            "新PCI": c.get("new_pci", c.get("pci")),
            "邻区数量": len(c.get("neighbors", [])),
            "邻区ECGI列表": ",".join(n["dst_ecgi"] for n in c.get("neighbors", [])),
            "邻区名称列表": ",".join(n["dst_name"] for n in c.get("neighbors", [])),
            "站点类型": c.get("site_type", "陆地"),
            "备注": "PCI变更" if c.get("pci") != c.get("new_pci") else "",
        }
        row.update(pci_quality_export_columns(quality_by.get(c.get("ecgi"))))
        rows.append(row)
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="规划后工参", index=False)
    return buf.getvalue()


def export_conflicts(conflicts: List[Dict[str, Any]]) -> bytes:
    """导出冲突报表"""
    rows = []
    for c in conflicts:
        rows.append({
            "小区A_ECGI": c["a"]["ecgi"],
            "小区A_名称": c["a"]["name"],
            "小区A_PCI": c["a"]["pci"],
            "小区A_制式": c["a"]["rat"],
            "小区B_ECGI": c["b"]["ecgi"],
            "小区B_名称": c["b"]["name"],
            "小区B_PCI": c["b"]["pci"],
            "小区B_制式": c["b"]["rat"],
            "冲突类型": c["type"],
            "严重度": c["severity"],
            "建议修复": c["suggestion"],
        })
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="冲突报表", index=False)
    return buf.getvalue()


def export_neighbors(cells: List[Dict[str, Any]]) -> bytes:
    """导出邻区清单(长格式)"""
    idx = _ecgi_index(cells)
    rows = []
    for c in cells:
        for n in c.get("neighbors", []):
            dst = idx.get(n["dst_ecgi"])
            rows.append(_neighbor_relation_row(c, n, dst, key_style="full"))
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="邻区清单", index=False)
    return buf.getvalue()


def export_mml(cells: List[Dict[str, Any]], vendor: str = "huawei") -> str:
    """
    导出MML脚本(华为/中兴)
    邻区关系配置命令
    """
    vendor = (vendor or "huawei").lower()
    lines: List[str] = []

    if vendor == "zte":
        lines.append("// 中兴MML - 邻区关系批量配置")
        lines.append("// 生成时间: " + pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"))
        lines.append("// 总邻区关系: %d" % sum(len(c.get("neighbors", [])) for c in cells))
        lines.append("")
        for c in cells:
            cell_id = c.get("ecgi")
            rat = c.get("rat", "LTE")
            for n in c.get("neighbors", []):
                nbr_id = n["dst_ecgi"]
                if rat == "NR":
                    lines.append(ZTE_NR_MML_TEMPLATE.format(cell_id=cell_id, nbr_id=nbr_id))
                else:
                    lines.append(ZTE_LTE_MML_TEMPLATE.format(cell_id=cell_id, nbr_id=nbr_id))
    else:
        # 华为
        lines.append("// 华为MML - 邻区关系批量配置")
        lines.append("// 生成时间: " + pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"))
        lines.append("// 总邻区关系: %d" % sum(len(c.get("neighbors", [])) for c in cells))
        lines.append("")
        for c in cells:
            cell_id = c.get("ecgi")
            rat = c.get("rat", "LTE")
            for n in c.get("neighbors", []):
                nbr_id = n["dst_ecgi"]
                if rat == "NR":
                    lines.append(HUAWEI_NR_MML_TEMPLATE.format(cell_id=cell_id, nbr_id=nbr_id))
                else:
                    lines.append(HUAWEI_LTE_MML_TEMPLATE.format(cell_id=cell_id, nbr_id=nbr_id))

    return "\n".join(lines)


def export_plan_summary(cells: List[Dict[str, Any]],
                        conflicts: List[Dict[str, Any]],
                        plan_log: List[str]) -> bytes:
    """导出规划总览(多sheet xlsx)"""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Sheet1: 工参
        rows = []
        for c in cells:
            rows.append({
                "ECGI": c.get("ecgi"),
                "小区名称": c.get("name"),
                "制式": c.get("rat"),
                "原PCI": c.get("pci"),
                "新PCI": c.get("new_pci", c.get("pci")),
                "邻区数": len(c.get("neighbors", [])),
            })
        pd.DataFrame(rows).to_excel(writer, sheet_name="工参总览", index=False)

        # Sheet2: 冲突
        if conflicts:
            rows = []
            for c in conflicts:
                rows.append({
                    "A": c["a"]["name"], "A_PCI": c["a"]["pci"],
                    "B": c["b"]["name"], "B_PCI": c["b"]["pci"],
                    "类型": c["type"], "严重度": c["severity"],
                    "建议": c["suggestion"],
                })
            pd.DataFrame(rows).to_excel(writer, sheet_name="冲突清单", index=False)

        # Sheet3: 规划日志
        pd.DataFrame({"规划日志": plan_log}).to_excel(writer, sheet_name="规划日志", index=False)

    return buf.getvalue()


# ──────────────────────────────────────────────
# 单/批量规划 多 sheet 导出
# Sheet 1: PCI规划表 (规划小区)
# Sheet 2..N: 邻区-<类型> (按邻区规划类型分组)
# ──────────────────────────────────────────────
def export_plan_split_sheets(
    cells: List[Dict[str, Any]],
    planned_ecgis: List[str],
    nbr_plan_types: Optional[List[str]] = None,
    *,
    check_mod30: bool = True,
    directional_filter: bool = True,
) -> bytes:
    """
    导出单/批量规划结果 (多 sheet xlsx)

    planned_ecgis: 本次规划的小区 ecgi 列表
    nbr_plan_types: 邻区规划类型枚举, 例 ["4G_4G", "4G_5G", "5G_4G", "5G_5G"]
        None 时默认全 4 种
    """
    if nbr_plan_types is None:
        nbr_plan_types = ["4G_4G", "4G_5G", "5G_4G", "5G_5G"]

    planned_set = set(planned_ecgis)
    planned_cells = [c for c in cells if c.get("ecgi") in planned_set]
    ecgi_idx = _ecgi_index(cells)
    quality_by = _quality_by_ecgi_for_export(
        cells,
        planned_set if planned_set else None,
        check_mod30=check_mod30,
        directional_filter=directional_filter,
    )

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Sheet 1: PCI规划表
        rows = []
        for c in planned_cells:
            # 候选 SSS 组 (按 nid1, 共 5 组) → 每组两列: 组号 + PCI 列表 (主选用 "*" 标记)
            pci_groups = c.get("pci_groups") or []
            row = {
                "ECGI": c.get("ecgi"),
                "小区名": c.get("name"),
                "站点名": c.get("site_name") or c.get("name", "").rsplit("-", 1)[0],
                "制式": c.get("rat"),
                "规划类型": c.get("plan_site_type") or c.get("site_type", "宏站"),
                "扇区号": c.get("sector_index", ""),
                "频段": c.get("freq_band") or c.get("freq_band_label", ""),
                "原PCI": c.get("pci"),
                "新PCI": c.get("new_pci", c.get("pci")),
                "PCI变更": "是" if c.get("pci") != c.get("new_pci") else "否",
                "经度": c.get("lon"),
                "纬度": c.get("lat"),
                "方位角": c.get("azimuth"),
                "波瓣(°)": c.get("beamwidth", c.get("beam")),
                "覆盖半径(m)": c.get("radius"),
                "扇区数": c.get("n_sectors", 1),
                "基方位角": c.get("base_azimuth", 0),
                "TAC": c.get("tac"),
                "锁定": "是" if c.get("locked") else "否",
            }
            # 5 组候选: 固定写, 不足 5 组时空字符串补齐
            # 主选标记: 该扇区当前已选的 new_pci 后跟 "*"
            current_pci = c.get("new_pci")
            for i in range(5):
                grp = pci_groups[i] if i < len(pci_groups) else None
                if grp:
                    pcis_str = ",".join(
                        f"{p}*" if current_pci is not None and int(p) == int(current_pci) else str(p)
                        for p in grp.get("pcis", [])
                    )
                    row[f"候选组{i+1}_号"] = grp.get("sss_group", "")
                    row[f"候选组{i+1}_得分"] = grp.get("score", "")
                    row[f"候选组{i+1}_PCI"] = pcis_str
                    row[f"候选组{i+1}_当前"] = "是" if grp.get("is_current") else "否"
                else:
                    row[f"候选组{i+1}_号"] = ""
                    row[f"候选组{i+1}_得分"] = ""
                    row[f"候选组{i+1}_PCI"] = ""
                    row[f"候选组{i+1}_当前"] = ""
            row.update(pci_quality_export_columns(quality_by.get(c.get("ecgi"))))
            rows.append(row)
        if rows:
            pd.DataFrame(rows).to_excel(writer, sheet_name="PCI规划表", index=False)
        else:
            pd.DataFrame({"说明": ["无规划小区"]}).to_excel(writer, sheet_name="PCI规划表", index=False)

        detail_rows = pci_quality_interference_detail_rows(
            planned_cells, quality_by, ecgi_idx,
        )
        if detail_rows:
            pd.DataFrame(detail_rows).to_excel(writer, sheet_name="PCI干扰明细", index=False)
        else:
            pd.DataFrame({"说明": ["无 PCI 干扰明细"]}).to_excel(writer, sheet_name="PCI干扰明细", index=False)

        # Sheet 2..N: 邻区-<类型>
        for nbr_type in nbr_plan_types:
            sheet_name = f"邻区-{nbr_type}"
            # Excel sheet 名限 31 字符
            sheet_name = sheet_name[:31]
            nbr_rows = []
            for c in planned_cells:
                for n in c.get("neighbors", []):
                    if n.get("nbr_type", "4G_4G") != nbr_type:
                        continue
                    dst = ecgi_idx.get(n["dst_ecgi"])
                    nbr_rows.append(
                        _neighbor_relation_row(c, n, dst, key_style="split")
                    )
            if nbr_rows:
                pd.DataFrame(nbr_rows).to_excel(writer, sheet_name=sheet_name, index=False)
            else:
                pd.DataFrame({"说明": [f"无 {nbr_type} 类型邻区"]}).to_excel(writer, sheet_name=sheet_name, index=False)

    return buf.getvalue()


# ──────────────────────────────────────────────
# 干扰分析报告 (xlsx)
# Sheet 1: 干扰清单
# Sheet 2: Mitigation Report
# ──────────────────────────────────────────────
def export_interference_report(
    issues: List[Dict[str, Any]],
    mitigation: str = "",
) -> bytes:
    """
    导出扇区干扰分析报告 (xlsx)
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Sheet 1: 干扰清单
        rows = []
        for it in issues:
            s1 = it.get("sector1", {})
            s2 = it.get("sector2", {})
            row = {
                "类型": it.get("type", ""),
                "严重度": it.get("severity", ""),
                "扇区1_ECGI": s1.get("ecgi", ""),
                "扇区1_名称": s1.get("name", ""),
                "扇区1_制式": s1.get("rat", ""),
                "扇区1_频段": s1.get("band", ""),
                "扇区1_PCI": s1.get("pci", ""),
                "扇区1_方位角": s1.get("azimuth", ""),
                "扇区1_波瓣": s1.get("beamwidth", ""),
                "扇区1_经度": s1.get("lon", ""),
                "扇区1_纬度": s1.get("lat", ""),
                "扇区2_ECGI": s2.get("ecgi", ""),
                "扇区2_名称": s2.get("name", ""),
                "扇区2_制式": s2.get("rat", ""),
                "扇区2_频段": s2.get("band", ""),
                "扇区2_PCI": s2.get("pci", ""),
                "扇区2_方位角": s2.get("azimuth", ""),
                "扇区2_波瓣": s2.get("beamwidth", ""),
                "扇区2_经度": s2.get("lon", ""),
                "扇区2_纬度": s2.get("lat", ""),
                "距离(km)": it.get("distance_km", 0),
                "Overlap1(%)": it.get("overlap1", 0),
                "Overlap2(%)": it.get("overlap2", 0),
                "冲突类型": it.get("conflict_type", ""),
                "频差(MHz)": it.get("freq_diff", ""),
                "说明": it.get("details", ""),
            }
            rows.append(row)
        if rows:
            pd.DataFrame(rows).to_excel(writer, sheet_name="干扰清单", index=False)
        else:
            pd.DataFrame({"说明": ["未检出干扰"]}).to_excel(writer, sheet_name="干扰清单", index=False)

        # Sheet 2: Mitigation
        if mitigation:
            pd.DataFrame({"Mitigation": mitigation.split("\n")}).to_excel(writer, sheet_name="Mitigation", index=False)
        else:
            pd.DataFrame({"Mitigation": [""]}).to_excel(writer, sheet_name="Mitigation", index=False)

    return buf.getvalue()