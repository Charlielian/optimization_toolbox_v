"""
PCI 规划作用域：按制式 (4G/5G) 与频段筛选待规划小区。
冲突检测仍使用全网其它小区的 PCI 作为锁定/外部参考。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from cell_filters import is_nb_znh_cell
from pci_evaluator import normalize_freq_band


def normalize_rat_filter(rat: Optional[str]) -> Optional[str]:
    """前端: LTE | NR | 空；返回 None 表示不限制。"""
    if not rat or not str(rat).strip():
        return None
    s = str(rat).strip().upper()
    if s in ("LTE", "4G", "EUTRAN"):
        return "LTE"
    if s in ("NR", "5G", "5G NR", "GNB"):
        return "NR"
    return s


def cell_freq_band_key(cell: Dict[str, Any]) -> str:
    raw = (
        cell.get("freq_band")
        or cell.get("freq_band_raw")
        or cell.get("plan_freq_band")
        or cell.get("freq_band_label")
        or ""
    )
    if not raw or str(raw).strip() in ("", "默认", "—", "-"):
        return "UNKNOWN"
    return normalize_freq_band(str(raw).strip())


def cells_same_freq_band_for_pci(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """
    PCI 干扰/冲突仅在同制式且同频段（同频）小区之间计算。
    LTE 与 NR 的 PCI 编号空间独立；不同频段（如 700M vs 2.6G）不互扰。
    """
    if a.get("rat", "LTE") != b.get("rat", "LTE"):
        return False
    return cell_freq_band_key(a) == cell_freq_band_key(b)


def cell_matches_pci_scope(
    cell: Dict[str, Any],
    rat_filter: Optional[str],
    freq_band_filter: Optional[str],
) -> bool:
    if is_nb_znh_cell(cell):
        return False
    rat_f = normalize_rat_filter(rat_filter)
    if rat_f:
        cell_rat = cell.get("rat", "LTE")
        if cell_rat != rat_f:
            return False
    if freq_band_filter and str(freq_band_filter).strip():
        want = normalize_freq_band(str(freq_band_filter).strip())
        if cell_freq_band_key(cell) != want:
            return False
    return True


def filter_cells_pci_scope(
    cells: List[Dict[str, Any]],
    rat_filter: Optional[str] = None,
    freq_band_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return [
        c for c in cells
        if cell_matches_pci_scope(c, rat_filter, freq_band_filter)
    ]


def filter_ecgis_pci_scope(
    cells: List[Dict[str, Any]],
    ecgis: List[str],
    rat_filter: Optional[str] = None,
    freq_band_filter: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """返回 (匹配的 ecgi, 因制式/频段被剔除的 ecgi)。"""
    by_ecgi = {c["ecgi"]: c for c in cells if c.get("ecgi")}
    matched: List[str] = []
    skipped: List[str] = []
    for e in ecgis:
        c = by_ecgi.get(e)
        if not c:
            continue
        if cell_matches_pci_scope(c, rat_filter, freq_band_filter):
            matched.append(e)
        else:
            skipped.append(e)
    return matched, skipped


def scope_log_label(rat_filter: Optional[str], freq_band_filter: Optional[str]) -> str:
    rat_f = normalize_rat_filter(rat_filter)
    parts = []
    if rat_f:
        parts.append("4G" if rat_f == "LTE" else "5G")
    if freq_band_filter and str(freq_band_filter).strip():
        parts.append(str(freq_band_filter).strip())
    return " + ".join(parts) if parts else "全部制式/频段"


def lock_out_of_scope_pcis(
    cells: List[Dict[str, Any]],
    rat_filter: Optional[str],
    freq_band_filter: Optional[str],
    locked: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    """作用域外小区：锁定当前 new_pci 或原 pci，不参与重分配。"""
    lk = dict(locked or {})
    for c in cells:
        ecgi = c.get("ecgi")
        if not ecgi or cell_matches_pci_scope(c, rat_filter, freq_band_filter):
            continue
        if ecgi in lk:
            continue
        if c.get("new_pci") is not None:
            lk[ecgi] = int(c["new_pci"])
        elif c.get("pci") is not None:
            try:
                p = int(c["pci"])
                if p >= 0:
                    lk[ecgi] = p
            except (TypeError, ValueError):
                pass
    return lk


def clear_new_pci_in_scope(
    cells: List[Dict[str, Any]],
    rat_filter: Optional[str],
    freq_band_filter: Optional[str],
    locked: Dict[str, int],
) -> int:
    """清除作用域内、且未锁定小区的 new_pci。返回清除数量。"""
    n = 0
    for c in cells:
        ecgi = c.get("ecgi")
        if not ecgi or not cell_matches_pci_scope(c, rat_filter, freq_band_filter):
            continue
        if ecgi in locked:
            continue
        if "new_pci" in c:
            del c["new_pci"]
            n += 1
    return n