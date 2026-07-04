"""
PCI冲突检测模块
4G LTE: 同PCI冲突、Mod3时序混淆冲突
5G NR:  同PCI冲突、Mod3时序冲突、Mod30 DMRS信道冲突
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def check_pair(pci_src: int, pci_dst: int, rat_src: str) -> Tuple[bool, str]:
    """
    检查一对PCI是否存在冲突
    返回 (是否冲突, 冲突类型描述)
    """
    if pci_src == pci_dst:
        return True, "同PCI冲突"
    if pci_src % 3 == pci_dst % 3:
        return True, "Mod3时序冲突"
    if rat_src == "NR" and pci_src % 30 == pci_dst % 30:
        return True, "Mod30 DMRS冲突"
    return False, ""


def cell_conflict_with_pools(pci: int, rat: str, conflict_pool: set) -> List[str]:
    """
    检查某PCI是否与冲突池中任意值冲突
    返回冲突类型列表
    """
    types: List[str] = []
    for other in conflict_pool:
        conf, msg = check_pair(pci, other, rat)
        if conf:
            types.append(msg)
    return types


# 用于 collect_conflicts 跳过计数（调用方通过 get_directional_skip_count 获取）
_directional_skip_count = 0


def get_directional_skip_count() -> int:
    """读取最近一次 collect_conflicts 中方向性过滤跳过的对数"""
    return _directional_skip_count


def collect_conflicts(cells: List[Dict[str, Any]], use_original_pci: bool = True,
                      same_pci_min_km: float = 5.0,
                      mod3_min_km: float = 1.0,
                      directional_filter: bool = True) -> List[Dict[str, Any]]:
    """
    全量遍历小区对,检出所有PCI冲突

    :param cells: 小区列表,每项含 ecgi/name/rat/pci 字段
    :param use_original_pci: True=使用原始PCI, False=使用规划后的new_pci
    :param same_pci_min_km: 同PCI的最小复用距离(低于此距离视为冲突)
    :param mod3_min_km: Mod3冲突的最小距离(低于此距离视为冲突)
    :param directional_filter: True=方向背向(双向)的小区对豁免冲突 (缺azimuth/beamwidth 时降级为不过滤)
    :return: 冲突列表
    """
    global _directional_skip_count
    _directional_skip_count = 0
    from cell_filters import filter_cells_for_map_and_plan
    from geo_utils import vincenty_distance, mutual_back_facing
    cells = filter_cells_for_map_and_plan(cells)
    pci_field = "pci" if use_original_pci else "new_pci"

    # 同制式分组
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in cells:
        groups.setdefault(c.get("rat", "LTE"), []).append(c)

    conflicts: List[Dict[str, Any]] = []

    for rat, group in groups.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a = group[i]
                b = group[j]
                pa = a.get(pci_field)
                pb = b.get(pci_field)
                if pa is None or pb is None:
                    continue
                pa_i, pb_i = int(pa), int(pb)
                # 计算距离
                d_km = vincenty_distance(a["lat"], a["lon"], b["lat"], b["lon"]) / 1000.0
                # 方向性豁免: 双向背向则跳过 (strict mode)
                if directional_filter and mutual_back_facing(a, b):
                    _directional_skip_count += 1
                    continue
                conf_msg = None
                if pa_i == pb_i and d_km < same_pci_min_km:
                    conf_msg = "同PCI冲突"
                elif pa_i % 3 == pb_i % 3 and d_km < mod3_min_km:
                    conf_msg = "Mod3时序冲突"
                elif rat == "NR" and pa_i % 30 == pb_i % 30 and d_km < mod3_min_km:
                    conf_msg = "Mod30 DMRS冲突"
                if conf_msg:
                    sev = _severity(a, b, rat, conf_msg)
                    suggestion = _suggestion(a, b, rat, conf_msg)
                    conflicts.append({
                        "a": {"ecgi": a["ecgi"], "name": a.get("name"), "pci": pa, "rat": rat, "distance_km": round(d_km, 2)},
                        "b": {"ecgi": b["ecgi"], "name": b.get("name"), "pci": pb, "rat": rat, "distance_km": round(d_km, 2)},
                        "type": conf_msg,
                        "severity": sev,
                        "suggestion": suggestion,
                    })

    return conflicts


def _severity(a: Dict[str, Any], b: Dict[str, Any], rat: str, msg: str) -> str:
    """
    冲突严重度
    - 同PCI: 高 (UE无法识别小区)
    - Mod3: 中 (下行参考信号混淆)
    - Mod30: 中 (DMRS信道混淆,仅NR)
    """
    if "同PCI" in msg:
        return "high"
    return "medium"


def _suggestion(a: Dict[str, Any], b: Dict[str, Any], rat: str, msg: str) -> str:
    """生成修复建议文本"""
    if "同PCI" in msg:
        return f"将 {b['name']}({b['ecgi']}) 的PCI调整为其它可用值,规避与 {a['name']} 同PCI"
    if "Mod3" in msg:
        return f"调整 {b['name']} 的PCI,使 pci%3 != {a['pci'] % 3}"
    if "Mod30" in msg:
        return f"调整 {b['name']} 的PCI,使 pci%30 != {a['pci'] % 30}"
    return "请人工核查"


def build_conflict_pci_set(cells: List[Dict[str, Any]], ecgi: str) -> set:
    """
    构建某小区的PCI冲突集合(用于PCI规划时排除)
    """
    target = next((c for c in cells if c["ecgi"] == ecgi), None)
    if not target:
        return set()

    pool: set = set()
    target_rat = target.get("rat", "LTE")
    for c in cells:
        if c["ecgi"] == ecgi:
            continue
        if c.get("rat") != target_rat:
            continue
        pci = c.get("new_pci", c.get("pci"))
        if pci is None:
            continue
        pool.add(int(pci))
    return pool


def stats_summary(conflicts: List[Dict[str, Any]]) -> Dict[str, int]:
    """汇总各类冲突数量"""
    s = {"total": len(conflicts), "high": 0, "medium": 0,
         "same_pci": 0, "mod3": 0, "mod30": 0}
    for c in conflicts:
        if c["severity"] == "high":
            s["high"] += 1
        else:
            s["medium"] += 1
        if "同PCI" in c["type"]:
            s["same_pci"] += 1
        elif "Mod3" in c["type"]:
            s["mod3"] += 1
        elif "Mod30" in c["type"]:
            s["mod30"] += 1
    return s