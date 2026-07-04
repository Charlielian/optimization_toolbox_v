"""
工参小区过滤：NB 等不参与地图展示与 PCI/邻区规划的小区。
名称（name）含 "-ZNH-" 视为 NB，一律排除。
"""
from __future__ import annotations

from typing import Any, Dict, List

NB_NAME_MARKER = "-ZNH-"


def is_nb_znh_cell(cell: Dict[str, Any]) -> bool:
    """小区 name 含 -ZNH- 的 NB 小区，不参与地图与规划。"""
    name = cell.get("name")
    if name is None:
        return False
    return NB_NAME_MARKER in str(name)


def filter_cells_for_map_and_plan(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """地图渲染、PCI 规划、邻区规划使用的小区列表。"""
    return [c for c in cells if not is_nb_znh_cell(c)]


def count_nb_znh_cells(cells: List[Dict[str, Any]]) -> int:
    return sum(1 for c in cells if is_nb_znh_cell(c))