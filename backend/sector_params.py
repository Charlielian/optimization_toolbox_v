"""
小区扇区参数映射

覆盖半径（邻区规划重叠判断用）:
  - SECTOR_RULES: (beam° 波瓣全角, coverage_m 覆盖半径)
  - 仅 plan_site_type 相关，与频段无关
  - 宏站 700m / 室分 100m / 微站 200m

扇区视觉半径（前端渲染用）:
  - VISUAL_RULES: (beam° 波瓣全角, visual_m 扇区视觉半径)
  - CASE 映射: 站点类型 + 制式 + 频段 → 扇区视觉半径（30-50m）
  - 用于前端扇区 polygon 大小 + 邻区连线端点偏移
  - 与 zoom 无关, 不受 UI 滑块影响

频段标准化: 工参原文 → 标准标签 (FDD900 / FDD1800 / F / D / A / 700M / 2.6G / 4.9G)
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple


# ──────────────────────────────────────────────
# 覆盖半径（邻区规划算法用，仅与站点类型相关）
# ──────────────────────────────────────────────
def _cr(beam: int, coverage_m: int) -> Tuple[int, int]:
    return (beam, coverage_m)


COVERAGE_RULES = {
    # 覆盖半径: 宏站 700m / 室分 100m / 微站 200m
    # beam° 用于规划扇区方位展开
    ("indoor",  None,    None):    _cr(359, 100),
    ("micro",   None,    None):    _cr(40,  200),
    ("outdoor", None,    None):    _cr(40,  700),   # 默认宏站
}

# ──────────────────────────────────────────────
# 扇区视觉半径（CASE 映射，前端渲染用）
# ──────────────────────────────────────────────
def _vr(beam: int, visual_m: int) -> Tuple[int, int]:
    return (beam, visual_m)


VISUAL_RULES = {
    # CASE 映射: 站点类型 + 制式 + 频段 → (beam° 波瓣全角, visual_m 扇区视觉半径)
    # 扇区视觉半径用于: 1) 前端扇区 polygon 大小  2) 邻区连线端点偏移

    # 室内分布系统
    ("indoor", None, None):     _vr(359, 30),

    # 微站
    ("micro", None, None):      _vr(40, 30),

    # 5G NR (户外 = 宏站)
    ("outdoor", "5G", "700M"):  _vr(40, 50),
    ("outdoor", "5G", "2.6G"):  _vr(65, 40),
    ("outdoor", "5G", "4.9G"):  _vr(70, 30),

    # 4G LTE (户外 = 宏站)
    ("outdoor", "4G", "FDD900"):  _vr(30, 47),
    ("outdoor", "4G", "FDD1800"): _vr(50, 43),
    ("outdoor", "4G", "F"):       _vr(45, 39),
    ("outdoor", "4G", "D"):       _vr(60, 42),
    ("outdoor", "4G", "A"):       _vr(55, 38),
    ("outdoor", "4G", "E"):       _vr(60, 38),

    # 默认 (未知频段)
    ("outdoor", "4G", None):      _vr(40, 40),
    ("outdoor", "5G", None):      _vr(65, 40),
    ("outdoor", None, None):      _vr(40, 40),
}


# ──────────────────────────────────────────────
# 频段标准化: 工参原文 → 标准标签
# ──────────────────────────────────────────────
_FREQ_PATTERNS: list = [
    # 5G NR (最具体优先)
    (re.compile(r"4\.9\s*GHz|n77(?!\d)|4\.9G", re.I),            "4.9G"),
    (re.compile(r"2\.6\s*GHz|n41|n78(?!\d)|2\.6G", re.I),       "2.6G"),
    (re.compile(r"700\s*M(?:Hz)?|n28|n5(?!\d)", re.I),          "700M"),

    # 4G LTE
    (re.compile(r"FDD\s*[-_]?\s*900|900\s*M(?:Hz)?|band\s*8|B8", re.I),  "FDD900"),
    (re.compile(r"FDD\s*[-_]?\s*1800|1800\s*M(?:Hz)?|band\s*3|B3", re.I), "FDD1800"),
    (re.compile(r"\bF[12]\b|F频段|频段\s*F|band\s*39|B39|TD[-_]?F", re.I),     "F"),
    (re.compile(r"\bD[0-9]\b|D频段|频段\s*D|band\s*38|B38|TD[-_]?D", re.I),     "D"),
    (re.compile(r"\bA\d?\b|A频段|频段\s*A|band\s*40|B40", re.I),                "A"),
    (re.compile(r"\bE[0-9]\b|E频段|频段\s*E|band\s*40", re.I),                  "E"),
]


def normalize_freq_band(rat: str, freq_text: Optional[str]) -> Optional[str]:
    """
    把工参里的频段文字归一化为: FDD900 / FDD1800 / F / D / A / E / 700M / 2.6G / 4.9G / None
    """
    if not freq_text:
        return None
    s = str(freq_text).strip()
    if not s or s.upper() in ("N/A", "NA", "NULL", "无", "-", "——"):
        return None
    for pat, label in _FREQ_PATTERNS:
        if pat.search(s):
            return label
    return None


# ──────────────────────────────────────────────
# 站点类型标准化
# ──────────────────────────────────────────────
def normalize_site_type(site_type: Optional[str]) -> str:
    if not site_type:
        return "outdoor"
    s = str(site_type).strip()
    if any(k in s for k in ["室", "INDOOR", "Indoor", "indoor", "分布"]):
        return "indoor"
    if any(k in s for k in ["微", "MICRO", "micro", "Micro", "Small Cell", "small_cell", "SmallCell"]):
        return "micro"
    if any(k in s for k in ["海", "SEA", "sea", "OFFSHORE", "offshore", "近海"]):
        return "offshore"
    return "outdoor"


# ──────────────────────────────────────────────
# 核心查询函数
# ──────────────────────────────────────────────
def get_sector_params(
    site_type: Optional[str],
    rat: Optional[str],
    freq_band: Optional[str],
) -> Tuple[int, int, str, str]:
    """
    返回: (beam°, coverage_m 覆盖半径, freq_band_label, site_type_label)

    coverage_m 仅与 plan_site_type 相关（由调用方传入 plan_site_type 而非 site_type），
    用于邻区规划重叠判断。
    """
    st = normalize_site_type(site_type)

    # 室分: 覆盖半径 100m, 频段无差异化
    if st == "indoor":
        beam, coverage = COVERAGE_RULES[("indoor", None, None)]
        return beam, coverage, "—", "室分"

    # 微站: 覆盖半径 200m
    if st == "micro":
        beam, coverage = COVERAGE_RULES[("micro", None, None)]
        return beam, coverage, freq_band or "默认", "微站"

    # 宏站: 覆盖半径 700m
    beam, coverage = COVERAGE_RULES[("outdoor", None, None)]

    rat_norm = None
    if rat in ("LTE", "4G", "FDD-LTE", "TDD-LTE", "EUTRA"):
        rat_norm = "4G"
    elif rat in ("NR", "5G", "5G NR", "gNB"):
        rat_norm = "5G"

    site_label = {
        "outdoor": "陆地",
        "indoor": "室分",
        "offshore": "海域",
    }.get(st, "陆地")

    return beam, coverage, freq_band or "默认", site_label


def get_visual_params(
    site_type: Optional[str],
    rat: Optional[str],
    freq_band: Optional[str],
) -> Tuple[int, int]:
    """
    返回: (beam° 波瓣全角, visual_m 扇区视觉半径)

    CASE 映射: 站点类型 + 制式 + 频段 → 扇区视觉半径
    用于前端扇区 polygon 大小 + 邻区连线端点偏移
    """
    st = normalize_site_type(site_type)

    # 室分
    if st == "indoor":
        return VISUAL_RULES[("indoor", None, None)]

    # 微站
    if st == "micro":
        return VISUAL_RULES[("micro", None, None)]

    rat_norm = None
    if rat in ("LTE", "4G", "FDD-LTE", "TDD-LTE", "EUTRA"):
        rat_norm = "4G"
    elif rat in ("NR", "5G", "5G NR", "gNB"):
        rat_norm = "5G"

    # 精确匹配 (outdoor, rat, freq_band)
    key = ("outdoor", rat_norm, freq_band)
    if key in VISUAL_RULES:
        return VISUAL_RULES[key]

    # 退到 (outdoor, rat, None)
    fallback = ("outdoor", rat_norm, None)
    if fallback in VISUAL_RULES:
        return VISUAL_RULES[fallback]

    return VISUAL_RULES[("outdoor", None, None)]


# ──────────────────────────────────────────────
# 把派生字段附加到 cell 字典
# ──────────────────────────────────────────────
def enrich_cell_with_sector(cell: Dict[str, Any]) -> Dict[str, Any]:
    """
    补充扇区参数:
      cell['beamwidth'] / cell['beam'] = 波瓣全角 (°) ← 来自 VISUAL_RULES (CASE 映射, 频段差异化)
      cell['radius']                    = 扇区视觉半径 (m) ← VISUAL_RULES (前端渲染用)
      cell['coverage_radius']           = 覆盖半径 (m) ← plan_site_type (邻区规划重叠算法用)
      cell['freq_band_label']           = 频段标签
      cell['site_type_label']           = 站点类型中文标签
      cell['oms_name']                  = 归属网管 ← 由 manufacturer + freq_band 派生
    """
    site_type = cell.get("site_type") or "陆地"
    rat = cell.get("rat")
    freq_band = cell.get("freq_band")

    # 覆盖半径: 仅与站点类型相关（邻区规划重叠算法用）
    _, coverage, freq_label, site_label = get_sector_params(site_type, rat, freq_band)

    # 扇区视觉参数: beam° + visual_m（来自 CASE 映射，频段差异化）
    visual_beam, visual_m = get_visual_params(site_type, rat, freq_band)

    cell["beamwidth"] = float(visual_beam)  # 前端 polygon 形状用 CASE beam
    cell["beam"] = float(visual_beam)
    cell["radius"] = float(visual_m)          # 扇区视觉半径 → 前端 polygon 大小 + 连线端点
    cell["coverage_radius"] = float(coverage) # 覆盖半径 → 邻区规划重叠算法
    cell["freq_band_label"] = freq_label
    cell["site_type_label"] = site_label
    # oms_name: 工参若已填写(归属网管列)则保留, 否则由 manufacturer + freq_band + name 派生
    if not cell.get("oms_name"):
        cell["oms_name"] = _derive_oms_name(
            cell.get("manufacturer"), freq_band, cell.get("name")
        )
    return cell


def _derive_oms_name(
    manufacturer: Optional[str],
    freq_band: Optional[str],
    name: Optional[str] = None,
) -> str:
    """
    归属网管派生:
      优先级 1: name 后缀 (最高权威)
        - 含 'CBN'                       → 700M网管 (700M 业务汇聚机房)
        - 含 -NLH-/-NLW-/-NLO-           → 诺基亚网管
        - 含 RGS-                         → 700M网管 (中兴 700M 业务汇聚)
        - 含 -SM-/-GZ-/RD-/RDC-, 或 Z 系列站标签
          (ZFH/ZLR/ZRW/ZRH/ZNH/ZLH/ZLW/ZFW/Z5H)
                                           → 2.6G网管 (视为中兴)
      优先级 2: freq_band == '700M'       → 700M网管
      优先级 3: manufacturer 字段
        - 含 '诺基亚'/'NOKIA'            → 诺基亚网管
        - 含 '中兴'                      → 2.6G网管
        - 空                              → 未知
        - 其他                            → 2.6G网管
    """
    m = (manufacturer or "").strip()
    fb = (freq_band or "").strip()
    nm = (name or "").strip().upper()

    # ---------- 优先级 1: name 后缀 ----------
    if nm:
        if "CBN" in nm:  # CBN 归属 700M网管, 优先于 freq_band
            return "700M网管"
        if any(p in nm for p in ["-NLH-", "-NLW-", "-NLO-"]):
            return "诺基亚网管"
        if "RGS-" in nm or "Z5H-" in nm:
            return "700M网管"
        if any(p in nm for p in [
            "-SM-", "-GZ-", "RD-", "RDC-",
            "ZFH-", "ZLR-", "ZRW-", "ZRH-", "ZNH-", "ZLH-", "ZLW-", "ZFW-",
        ]):
            return "2.6G网管"

    # ---------- 优先级 2: 频段为 700M ----------
    if fb == "700M":
        return "700M网管"

    # ---------- 优先级 3: manufacturer 字段 ----------
    if not m:
        return "未知"
    if "诺基亚" in m or "NOKIA" in m.upper():
        return "诺基亚网管"
    if "中兴" in m:
        return "2.6G网管"
    return "2.6G网管"


def get_coverage_radius_by_plan_type(plan_site_type: Optional[str]) -> float:
    """
    按 plan_site_type 返回覆盖半径（米）
    用于 expand_site_to_cells / 邻区规划重叠判断
    """
    st = normalize_site_type(plan_site_type or "")
    if st == "indoor":
        return 100.0
    if st == "micro":
        return 200.0
    return 700.0  # 宏站/默认
