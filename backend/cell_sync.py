"""
同步网管基础数据到 cells 表

数据来源:
  - cfg_CUEUtranCellFDDLTE  (4G FDD 小区)
  - cfg_CUEUtranCellTDDLTE  (4G TDD 小区)
  - cfg_EUtranCellFDD       (4G FDD 小区，另一种模型)

步骤:
  1) 从每行 ldn 提取第一段(如 ENBCUCPFunction=... 或 ENBCUCPFunction=460-00_306041)
  2) 提取 enbid: 去掉前缀 ENBCUCPFunction= / EUtranCellFDD=, 替换 460-00_/-460-00 为空
  3) 拼接 cgi = '460-00-' + enbid + '-' + cell_local_id
  4) earfcn_dl / earfcn 通过映射表转换为 (earfcn, freqBandInd)
  5) 合并3张表的 cgi+清单
  6) 以 cgi 关联 cells.ecgi, 更新 pci / tac / earfcn / freq_band_ind
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from db import _connect

logger = logging.getLogger(__name__)

# earfcn_dl / earfcn -> (earfcn, freqBandInd)
# 来源: 网管基础数据转换规则
_EARFCN_DL_TO_EARFCN: Dict[float, Tuple[int, int]] = {
    939:    (3590,  8),
    1815:   (1300,  3),
    1815.1: (1301,  3),
    2624.6: (40936, 41),
    2330:   (38950, 40),
    2644.4: (41134, 41),
    2624.7: (40937, 41),
    2645.3: (41143, 41),
    38400:  (38400, 39),
    40936:  (40936, 41),
}

# 输入精度: earfcn_dl/earfcn 在表里既可能是 1815 也可能是 1815.1 / 2624.6 等浮点
# 用容差比较 (浮点近似等于) 来避免精度丢失导致查不到
_EARFCN_EPS = 0.05


def _normalize_enbid_from_ldn(ldn: str) -> Optional[str]:
    """从 ldn 字段提取 enbid

    规则:
      - 取第一逗号前的第一段 (e.g. "ENBCUCPFunction=564647-460-00")
      - 剔除前缀 "ENBCUCPFunction=" / "EUtranCellFDD=" 等, 剩余 "564647-460-00"
        或 "460-00_306041"
      - 替换 "460-00_" / "-460-00" 为空, 剩余的即 enbid

    兼容前缀: ENBCUCPFunction= (网管) 或 EUtranCellFDD= (MML样例)
    """
    if not ldn:
        return None
    s = str(ldn).strip()
    if not s:
        return None
    # 取第一逗号前
    head = s.split(",", 1)[0].strip()
    if not head:
        return None
    # 剔除前缀 (取最后一个 '=' 之后的内容, 以容忍前缀中可能含 '=')
    if "=" in head:
        head = head.rsplit("=", 1)[1].strip()
    if not head:
        return None
    # 替换前缀关键字 460-00_ / -460-00
    cleaned = head.replace("460-00_", "").replace("-460-00", "")
    cleaned = cleaned.strip()
    return cleaned or None


def _safe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        # 1815.1 -> 1815 (PCI/TAC 不该是浮点, 但 earfcn 可能)
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lookup_earfcn_mapping(raw_value: Any) -> Optional[Tuple[int, int]]:
    """根据 earfcn_dl/earfcn 查映射表，返回 (earfcn, freqBandInd)"""
    v = _safe_float(raw_value)
    if v is None:
        return None
    for k, mapped in _EARFCN_DL_TO_EARFCN.items():
        if abs(k - v) <= _EARFCN_EPS:
            return mapped
    return None


# 每个 cfg_ 表的列提取规则
_SOURCE_TABLES = [
    {
        "table": "cfg_CUEUtranCellFDDLTE",
        "ldn_col": "ldn",
        "cell_local_id_col": "cell_local_id",
        "pci_col": "pci",
        "tac_col": "tac",
        "earfcn_col": "earfcn_dl",     # FDD: earfcn_dl
    },
    {
        "table": "cfg_CUEUtranCellTDDLTE",
        "ldn_col": "ldn",
        "cell_local_id_col": "cell_local_id",
        "pci_col": "pci",
        "tac_col": "tac",
        "earfcn_col": "earfcn",        # TDD: earfcn
    },
    {
        "table": "cfg_EUtranCellFDD",
        "ldn_col": None,                # 此表没有 ldn 字段, 直接用 me_id 当 enbid
        "me_id_col": "me_id",          # enbid = me_id
        "cell_local_id_col": "cell_local_id",
        "pci_col": "pci",
        "tac_col": "tac",
        "earfcn_col": "earfcn_dl",
    },
]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def _read_source_rows(conn: sqlite3.Connection, spec: Dict[str, str]) -> List[Dict[str, Any]]:
    """从 cfg_ 表读取所有相关行，返回统一格式 [{cgi, pci, tac, earfcn_dl}, ...]"""
    table = spec["table"]
    if not _table_exists(conn, table):
        logger.warning("[sync] 表 %s 不存在，跳过", table)
        return []

    # 仅读取需要的列 (动态判断是否存在)
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing_cols = {row[1] for row in cur.fetchall()}
    needed = [spec["cell_local_id_col"], spec["pci_col"], spec["tac_col"], spec["earfcn_col"]]
    if spec.get("ldn_col"):
        needed.append(spec["ldn_col"])
    if spec.get("me_id_col"):
        needed.append(spec["me_id_col"])
    select_cols = [c for c in needed if c in existing_cols]
    if spec["cell_local_id_col"] not in select_cols:
        logger.warning("[sync] 表 %s 缺少 %s 列，跳过", table, spec["cell_local_id_col"])
        return []
    sql = f"SELECT {','.join(select_cols)} FROM {table}"
    rows = conn.execute(sql).fetchall()
    # row_factory = sqlite3.Row, 转为 dict
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = {k: r[k] for k in select_cols}
        out.append(d)
    return out


def _build_cgi_list(rows: List[Dict[str, Any]], spec: Dict[str, str]) -> List[Dict[str, Any]]:
    """把源行转换为统一 cgi 清单: [{cgi, pci, tac, earfcn_dl, src_table}]"""
    cell_local_id_col = spec["cell_local_id_col"]
    pci_col = spec["pci_col"]
    tac_col = spec["tac_col"]
    earfcn_col = spec["earfcn_col"]
    ldn_col = spec.get("ldn_col")
    me_id_col = spec.get("me_id_col")

    result: List[Dict[str, Any]] = []
    for row in rows:
        cell_local_id = row.get(cell_local_id_col)
        if cell_local_id is None or cell_local_id == "":
            continue
        cell_local_id = str(cell_local_id).strip()
        if not cell_local_id:
            continue

        # enbid 提取
        enbid = None
        if ldn_col and row.get(ldn_col):
            enbid = _normalize_enbid_from_ldn(row[ldn_col])
        elif me_id_col:
            me = row.get(me_id_col)
            if me is not None:
                # me_id 直接作为 enbid (清理非数字字符)
                enbid = re.sub(r"\D", "", str(me))
        if not enbid:
            continue

        cgi = f"460-00-{enbid}-{cell_local_id}"
        result.append({
            "cgi": cgi,
            "pci": _safe_int(row.get(pci_col)),
            "tac": _safe_int(row.get(tac_col)),
            "earfcn_dl": _safe_float(row.get(earfcn_col)),
            "src_table": spec["table"],
        })
    return result


def build_cgi_list() -> Dict[str, Any]:
    """读取3张源表, 返回合并的 cgi 清单

    Returns:
        {
            "list": [{cgi, pci, tac, earfcn_dl, mapped_earfcn, mapped_freq_band_ind}, ...],
            "stats": {per_table: row_count, total: ...},
            "missing_tables": [未导入的表名],
            "earfcn_unmapped": [未在映射表中命中的 cgi 列表]
        }
    """
    raw_list: List[Dict[str, Any]] = []
    stats: Dict[str, int] = {}
    missing_tables: List[str] = []

    with _connect() as conn:
        for spec in _SOURCE_TABLES:
            rows = _read_source_rows(conn, spec)
            if not _table_exists(conn, spec["table"]):
                missing_tables.append(spec["table"])
            built = _build_cgi_list(rows, spec)
            raw_list.extend(built)
            stats[spec["table"]] = len(built)

    # 应用 earfcn_dl/earfcn 映射
    unmapped: List[Dict[str, Any]] = []
    for item in raw_list:
        mapped = _lookup_earfcn_mapping(item.get("earfcn_dl"))
        if mapped is None:
            item["mapped_earfcn"] = None
            item["mapped_freq_band_ind"] = None
            unmapped.append({
                "cgi": item["cgi"],
                "earfcn_dl": item["earfcn_dl"],
                "src_table": item["src_table"],
            })
        else:
            item["mapped_earfcn"] = mapped[0]
            item["mapped_freq_band_ind"] = mapped[1]

    # 同 cgi 重复时 (FDD/TDD 同 cell_local_id + 同 enbid), 优先保留 mapped 成功的
    by_cgi: Dict[str, Dict[str, Any]] = {}
    for item in raw_list:
        cgi = item["cgi"]
        existing = by_cgi.get(cgi)
        if existing is None:
            by_cgi[cgi] = item
            continue
        # 已有 entry, 比较优先级: mapped_earfcn 成功 > 后到的 (简单策略: 后到的覆盖)
        # 实际上 3 张表 ENBID 提取规则略有差异, 重复极少, 保留最后一条
        by_cgi[cgi] = item

    final_list = list(by_cgi.values())

    return {
        "list": final_list,
        "stats": stats,
        "missing_tables": missing_tables,
        "earfcn_unmapped": unmapped,
    }


def apply_sync_to_cells(
    cgi_list: List[Dict[str, Any]],
    only_with_earfcn_mapping: bool = True,
) -> Dict[str, Any]:
    """把 cgi 清单同步到 cells 表

    匹配规则: cgi == cells.ecgi (e.g. 460-00-306041-1 == 460-00-306041-1)
    更新字段:
        pci             <- list.pci
        tac             <- list.tac
        earfcn          <- list.mapped_earfcn (来自 earfcn_dl 映射)
        freq_band_ind   <- list.mapped_freq_band_ind
        pci_synced_at   <- 当前时间戳 (ISO8601), 作为"已被同步标记"

    only_with_earfcn_mapping=True: 当 mapped_earfcn 为空时, 不更新 earfcn/freq_band_ind
                                   但仍会更新 pci/tac
                                   (cgi 在 3 张表之间冲突时可避免错乱)
    """
    updated_pci_tac = 0
    updated_earfcn_band = 0
    marked_synced = 0
    unmatched = 0
    matched_ecgis: List[str] = []

    now = None
    try:
        from datetime import datetime
        now = datetime.utcnow().isoformat(timespec="seconds")
    except Exception:
        pass

    with _connect() as conn:
        conn.execute("BEGIN")
        try:
            for item in cgi_list:
                cgi = item.get("cgi")
                if not cgi:
                    unmatched += 1
                    continue
                cur = conn.execute("SELECT ecgi FROM cells WHERE ecgi=?", (cgi,))
                if not cur.fetchone():
                    unmatched += 1
                    continue

                pci = item.get("pci")
                tac = item.get("tac")
                mapped_earfcn = item.get("mapped_earfcn")
                mapped_band = item.get("mapped_freq_band_ind")

                # 1) 先更新 pci/tac + 同步时间戳 (这两个是清晰字段)
                if pci is not None or tac is not None:
                    sets = ["pci_synced_at = ?"]
                    params: List[Any] = [now]
                    if pci is not None:
                        sets.append("pci = ?")
                        params.append(pci)
                    if tac is not None:
                        sets.append("tac = ?")
                        params.append(tac)
                    if now:
                        sets.append("updated_at = ?")
                        params.append(now)
                    params.append(cgi)
                    sql = f"UPDATE cells SET {', '.join(sets)} WHERE ecgi = ?"
                    conn.execute(sql, params)
                    updated_pci_tac += 1
                    marked_synced += 1
                    matched_ecgis.append(cgi)

                # 2) 再更新 earfcn / freq_band_ind (仅当映射命中)
                if mapped_earfcn is not None and mapped_band is not None:
                    sets = ["earfcn = ?", "freq_band_ind = ?", "pci_synced_at = ?"]
                    params = [mapped_earfcn, mapped_band, now]
                    if now:
                        sets.append("updated_at = ?")
                        params.append(now)
                    params.append(cgi)
                    sql = f"UPDATE cells SET {', '.join(sets)} WHERE ecgi = ?"
                    conn.execute(sql, params)
                    updated_earfcn_band += 1

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return {
        "matched": len(matched_ecgis),
        "unmatched": unmatched,
        "updated_pci_tac": updated_pci_tac,
        "updated_earfcn_band": updated_earfcn_band,
        "marked_synced": marked_synced,
    }


def sync_cells_from_config() -> Dict[str, Any]:
    """一键同步: 读 cgi 清单 + 写 cells 表"""
    build = build_cgi_list()
    cgi_list = build["list"]
    if not cgi_list:
        return {
            "success": False,
            "message": "未生成任何 cgi 清单, 请检查 cfg_ 三张表是否已导入",
            "stats": {"cgi_count": 0},
            "build_stats": build["stats"],
            "missing_tables": build["missing_tables"],
        }
    apply_stats = apply_sync_to_cells(cgi_list)

    # 同步内存中的 STATE.cells, 让 /api/cells 立刻看到 pci_synced_at 等字段
    try:
        from main import STATE  # 延迟导入避免循环
        cgi_index = {item.get("cgi"): item for item in cgi_list if item.get("cgi")}
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        for cell in STATE.cells:
            ecgi = cell.get("ecgi")
            if not ecgi:
                continue
            item = cgi_index.get(ecgi)
            if not item:
                continue
            pci = item.get("pci")
            tac = item.get("tac")
            mapped_earfcn = item.get("mapped_earfcn")
            mapped_band = item.get("mapped_freq_band_ind")
            if pci is not None or tac is not None:
                if pci is not None:
                    cell["pci"] = pci
                if tac is not None:
                    cell["tac"] = tac
                cell["pci_synced_at"] = now_iso
                cell["updated_at"] = now_iso
            if mapped_earfcn is not None and mapped_band is not None:
                cell["earfcn"] = mapped_earfcn
                cell["freq_band_ind"] = mapped_band
                cell["pci_synced_at"] = now_iso
                cell["updated_at"] = now_iso
    except Exception as _e:
        logger.warning("同步 STATE.cells 内存失败 (DB 已更新, 仅内存未刷新): %s", _e)

    return {
        "success": True,
        "stats": {
            "cgi_count": len(cgi_list),
            "matched_ecgis": apply_stats["matched"],
            "unmatched_ecgis": apply_stats["unmatched"],
            "updated_pci_tac": apply_stats["updated_pci_tac"],
            "updated_earfcn_band": apply_stats["updated_earfcn_band"],
            "marked_synced": apply_stats["marked_synced"],
        },
        "build_stats": build["stats"],
        "missing_tables": build["missing_tables"],
        "earfcn_unmapped_count": len(build["earfcn_unmapped"]),
        "earfcn_unmapped_sample": build["earfcn_unmapped"][:50],  # 限制返回前50条
    }