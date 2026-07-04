"""
SQLite 持久化层

- 启动时调用 init_db() 建表
- 每次 STATE.cells 变更后调用 save_all() 整体覆盖入库
- 启动时调用 load_all() 把上次的工参+规划结果恢复到内存
- /api/clear 触发 clear_all() 清空

数据规模: 单数据集 ~3 万行, 单机 SQLite 单线程写, 全量 DELETE+INSERT < 200ms
邻区列表以 JSON 字符串内联存储到 cells.neighbors_json 字段, 避免关联表
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sector_params import enrich_cell_with_sector

# 数据库文件位于项目根目录 data/plan.db
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "plan.db"

# cells 表持久化字段 (不含 neighbors 列表, 单独存 JSON)
_CELL_COLUMNS = [
    "ecgi", "name", "rat", "earfcn", "lon", "lat", "azimuth", "radius",
    "tac", "pci", "new_pci", "cell_id", "site_name", "phy_name", "ant_name",
    "manufacturer", "oms_name", "site_type", "freq_band", "freq_band_label",
    "freq_band_ind", "bandwidth", "beam", "beamwidth", "pci_missing",
    "n_sectors", "plan_site_type", "base_azimuth", "locked", "pci_synced_at",
]

_SCHEMA_CELLS = """
CREATE TABLE IF NOT EXISTS cells (
    ecgi              TEXT PRIMARY KEY,
    name              TEXT,
    rat               TEXT,
    earfcn            INTEGER,
    lon               REAL,
    lat               REAL,
    azimuth           REAL,
    radius            REAL,
    tac               INTEGER,
    pci               INTEGER,
    new_pci           INTEGER,
    cell_id           TEXT,
    site_name         TEXT,
    phy_name          TEXT,
    ant_name          TEXT,
    manufacturer      TEXT,
    oms_name          TEXT,
    site_type         TEXT,
    freq_band         TEXT,
    freq_band_label   TEXT,
    freq_band_ind     INTEGER,
    bandwidth         REAL,
    beam              TEXT,
    beamwidth         REAL,
    pci_missing       INTEGER DEFAULT 0,
    n_sectors         INTEGER DEFAULT 1,
    plan_site_type    TEXT,
    base_azimuth      REAL DEFAULT 0,
    locked            INTEGER DEFAULT 0,
    pci_synced_at     TEXT,
    neighbors_json    TEXT,
    updated_at        TEXT
);
"""

# 索引单独提取: 必须在 _ensure_columns 之后建, 否则老库缺列时建索引会失败
_SCHEMA_CELLS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_cells_rat ON cells(rat);
CREATE INDEX IF NOT EXISTS idx_cells_site ON cells(site_name);
CREATE INDEX IF NOT EXISTS idx_cells_manufacturer ON cells(manufacturer);
"""

_SCHEMA_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect() -> sqlite3.Connection:
    """打开连接, 配置 WAL + foreign_keys"""
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """启动时调用: 建表 + 列迁移 + 索引(顺序很重要: 先补列再建索引)"""
    with _connect() as conn:
        conn.executescript(_SCHEMA_CELLS)
        conn.executescript(_SCHEMA_META)
        _ensure_columns(conn)              # 1. 先给老库 ADD COLUMN 新列
        conn.executescript(_SCHEMA_CELLS_INDEX)  # 2. 再建依赖新列的索引


def _coerce_for_sql(value: Any, col: str) -> Any:
    """把 Python 类型转成 SQLite 友好的值"""
    if value is None:
        return None
    if col in ("lon", "lat", "azimuth", "radius", "bandwidth", "base_azimuth", "beamwidth"):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if col in ("earfcn", "tac", "pci", "new_pci", "n_sectors", "freq_band_ind"):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if col in ("pci_missing", "locked"):
        return 1 if value else 0
    return str(value) if not isinstance(value, str) else value


# 列名 → (DDL 关键字)  用于 ALTER TABLE 迁移
_COL_DDL = {
    "n_sectors": "INTEGER DEFAULT 1",
    "plan_site_type": "TEXT",
    "base_azimuth": "REAL DEFAULT 0",
    "locked": "INTEGER DEFAULT 0",
    "beamwidth": "REAL",
    "manufacturer": "TEXT",
    "phy_name": "TEXT",
    "ant_name": "TEXT",
    "oms_name": "TEXT",
    "freq_band_ind": "INTEGER",
    "pci_synced_at": "TEXT",
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """兼容老 DB: 自动 ALTER TABLE ADD COLUMN"""
    cur = conn.execute("PRAGMA table_info(cells)")
    existing = {row[1] for row in cur.fetchall()}
    for col, ddl in _COL_DDL.items():
        if col in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE cells ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass  # 列已存在 / 不可加, 忽略


def save_all(cells: List[Dict[str, Any]], meta: Dict[str, Any] = None) -> int:
    """
    整体覆盖 cells 表 + 写入 meta.
    跳过标记为 is_temp=True 的临时小区 (plan_single_site 生成的 PLAN-* 小区不入库).

    Returns: 写入的行数
    """
    meta = meta or {}
    now = datetime.utcnow().isoformat(timespec="seconds")

    # 过滤掉临时小区 (PLAN-* 生成的规划小区不持久化)
    persistable = [c for c in cells if not c.get("is_temp")]

    rows = []
    for c in persistable:
        row = []
        for col in _CELL_COLUMNS:
            row.append(_coerce_for_sql(c.get(col), col))
        row.append(json.dumps(c.get("neighbors") or [], ensure_ascii=False))
        row.append(now)
        rows.append(tuple(row))

    insert_sql = (
        f"INSERT OR REPLACE INTO cells ({','.join(_CELL_COLUMNS)}, neighbors_json, updated_at) "
        f"VALUES ({','.join('?' * (len(_CELL_COLUMNS) + 2))})"
    )

    with _connect() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM cells")
            if rows:
                conn.executemany(insert_sql, rows)
            for k, v in meta.items():
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (k, json.dumps(v, ensure_ascii=False, default=str)),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return len(rows)


def load_all() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    从 DB 读出所有 cells 和 meta.

    Returns: (cells_list, meta_dict). 空库返回 ([], {}).

    ⚠ 启动时强制用 CASE 映射纠正所有 cell 的扇区视觉半径:
       数据库里可能存着旧的 radius (如覆盖半径 700m)，必须 enrich 后才返回。
    """
    with _connect() as conn:
        cur = conn.execute(f"SELECT {','.join(_CELL_COLUMNS)}, neighbors_json FROM cells")
        cells = []
        for r in cur.fetchall():
            row = dict(r)
            try:
                row["neighbors"] = json.loads(row.pop("neighbors_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                row["neighbors"] = []
            row["pci_missing"] = bool(row.get("pci_missing"))
            row["locked"] = bool(row.get("locked"))
            # 过滤掉旧 DB 里残留的 PLAN-* 临时小区 (is_temp 字段不在 _CELL_COLUMNS, 必然是旧数据)
            ecgi = row.get("ecgi") or ""
            if ecgi.startswith("PLAN-"):
                continue
            cells.append(row)

        meta = {}
        for r in conn.execute("SELECT key, value FROM meta").fetchall():
            try:
                meta[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                meta[r["key"]] = r["value"]

    # ── 启动时强制用 CASE 映射纠正所有 cell 的扇区视觉半径 ──────────
    # 保证即使数据库里有旧 radius (如覆盖半径 700m)，前端也拿到正确的扇区视觉半径
    for c in cells:
        enrich_cell_with_sector(c)

    return cells, meta


def clear_all() -> None:
    """清空 cells 和 meta 表"""
    with _connect() as conn:
        conn.execute("DELETE FROM cells")
        conn.execute("DELETE FROM meta")


# ──────────────────────────────────────────────
# 配置导入相关的数据库操作
# ──────────────────────────────────────────────

# 配置导入历史表
_SCHEMA_CONFIG_IMPORTS = """
CREATE TABLE IF NOT EXISTS config_imports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    sheet_name      TEXT NOT NULL,
    row_count       INTEGER DEFAULT 0,
    imported_at     TEXT NOT NULL,
    status          TEXT DEFAULT 'success',
    error_msg       TEXT
);
CREATE INDEX IF NOT EXISTS idx_config_imports_filename ON config_imports(filename);
CREATE INDEX IF NOT EXISTS idx_config_imports_sheet ON config_imports(sheet_name);
"""

# 配置表元信息（记录每个配置表的结构）
_SCHEMA_CONFIG_TABLES = """
CREATE TABLE IF NOT EXISTS config_tables (
    table_name      TEXT PRIMARY KEY,
    description     TEXT,
    columns_json    TEXT,        -- JSON存储列信息
    pk_columns_json TEXT,        -- JSON存储主键列名列表
    row_count       INTEGER DEFAULT 0,
    last_imported   TEXT,
    source_file     TEXT
);
"""

# 列配置管理表（用于Web界面管理）
_SCHEMA_COLUMN_CONFIG = """
CREATE TABLE IF NOT EXISTS column_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_name      TEXT NOT NULL,
    column_src      TEXT NOT NULL,   -- Excel列名
    column_dst      TEXT NOT NULL,   -- 数据库列名
    data_type       TEXT DEFAULT 'TEXT',
    is_pk           INTEGER DEFAULT 0,
    is_enabled      INTEGER DEFAULT 1,
    display_order   INTEGER DEFAULT 0,
    created_at      TEXT,
    updated_at      TEXT,
    UNIQUE(sheet_name, column_src)
);
CREATE INDEX IF NOT EXISTS idx_column_config_sheet ON column_config(sheet_name);
"""


def init_config_db() -> None:
    """初始化配置相关的表"""
    with _connect() as conn:
        conn.executescript(_SCHEMA_CONFIG_IMPORTS)
        conn.executescript(_SCHEMA_CONFIG_TABLES)
        conn.executescript(_SCHEMA_COLUMN_CONFIG)


def save_config_sheet(
    sheet_name: str,
    columns: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    pk_columns: List[str],
    filename: str = "",
    progress_cb: Optional[Any] = None,
) -> int:
    """
    保存配置sheet数据到数据库

    Args:
        sheet_name: sheet名称（也是表名前缀）
        columns: 列信息列表 [{"name": 列名, "type": 类型, "is_pk": bool}, ...]
        rows: 数据行列表
        pk_columns: 主键列名列表
        filename: 来源文件名
        progress_cb: 可选进度回调 fn(done_rows: int) -> None

    Returns:
        写入的行数
    """
    table_name = f"cfg_{sheet_name}"
    now = datetime.utcnow().isoformat(timespec="seconds")
    total_rows = len(rows)
    # 批次大小: 500 行/批，进度回调粒度
    BATCH_SIZE = 500

    def _emit_progress(done: int) -> None:
        if progress_cb is not None:
            try:
                progress_cb(done)
            except Exception:
                pass

    with _connect() as conn:
        conn.execute("BEGIN")
        try:
            # 1. 检查表是否存在，不存在则创建
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            table_exists = cur.fetchone() is not None

            if not table_exists:
                # 动态建表
                col_defs = []
                pk_inline_columns: List[str] = []
                for col in columns:
                    col_name = col["name"]
                    col_type = col.get("type", "TEXT")
                    is_pk = col.get("is_pk", False)
                    if is_pk:
                        col_defs.append(f"{col_name} {col_type}")
                        pk_inline_columns.append(col_name)
                    else:
                        col_defs.append(f"{col_name} {col_type}")
                if pk_columns:
                    # 多主键: 表级 PRIMARY KEY (col1, col2, ...)
                    create_sql = (
                        f"CREATE TABLE {table_name} ("
                        f"{','.join(col_defs)}, "
                        f"PRIMARY KEY ({','.join(pk_inline_columns)})"
                        f")"
                    )
                else:
                    # 没有主键时, 添加自增主键
                    create_sql = (
                        f"CREATE TABLE {table_name} ("
                        f"id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        f"{','.join(col_defs)}"
                        f")"
                    )
                conn.execute(create_sql)

            # 2. 写入数据（分批以支持进度回调）
            if rows:
                col_names = [c["name"] for c in columns]
                placeholders = ",".join("?" * len(col_names))

                if pk_columns:
                    # 有主键：使用 INSERT OR REPLACE（去重更新）
                    insert_sql = f"INSERT OR REPLACE INTO {table_name} ({','.join(col_names)}) VALUES ({placeholders})"
                else:
                    # 无主键：先清空再插入
                    conn.execute(f"DELETE FROM {table_name}")
                    insert_sql = f"INSERT INTO {table_name} ({','.join(col_names)}) VALUES ({placeholders})"

                # 准备列名索引（用于快速取值）
                col_indices = list(range(len(col_names)))

                done = 0
                _emit_progress(0)
                for start in range(0, total_rows, BATCH_SIZE):
                    batch = rows[start:start + BATCH_SIZE]
                    data_batch = [
                        tuple(row.get(col) for col in col_names)
                        for row in batch
                    ]
                    conn.executemany(insert_sql, data_batch)
                    done += len(batch)
                    _emit_progress(done)

                # 确保最后一次 progress 是 total
                if done != total_rows:
                    _emit_progress(total_rows)

            # 3. 更新元信息表
            conn.execute(
                """INSERT OR REPLACE INTO config_tables
                   (table_name, description, columns_json, pk_columns_json, row_count, last_imported, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    table_name,
                    "",
                    json.dumps(columns, ensure_ascii=False),
                    json.dumps(pk_columns, ensure_ascii=False),
                    len(rows),
                    now,
                    filename,
                )
            )

            # 4. 记录导入历史
            conn.execute(
                """INSERT INTO config_imports
                   (filename, sheet_name, row_count, imported_at, status)
                   VALUES (?, ?, ?, ?, 'success')""",
                (filename, sheet_name, len(rows), now)
            )

            conn.execute("COMMIT")
        except Exception as e:
            conn.execute("ROLLBACK")
            # 记录失败历史
            conn.execute(
                """INSERT INTO config_imports
                   (filename, sheet_name, row_count, imported_at, status, error_msg)
                   VALUES (?, ?, 0, ?, 'failed', ?)""",
                (filename, sheet_name, now, str(e))
            )
            raise

    return len(rows)


def get_config_table_data(
    table_name: str,
    page: int = 1,
    page_size: int = 50,
    keyword: str = None,
) -> Dict[str, Any]:
    """
    查询配置表数据（分页）

    Args:
        table_name: 表名（不含cfg_前缀）
        page: 页码（从1开始）
        page_size: 每页条数
        keyword: 搜索关键字（模糊匹配所有文本列）

    Returns:
        {
            "rows": [...],
            "total": int,
            "page": int,
            "page_size": int,
            "columns": [...],
        }
    """
    full_table = f"cfg_{table_name}"
    offset = (page - 1) * page_size

    with _connect() as conn:
        # 检查表是否存在
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (full_table,)
        )
        if not cur.fetchone():
            return {"rows": [], "total": 0, "page": page, "page_size": page_size, "columns": [], "column_meta": []}

        # 获取列信息
        meta_cur = conn.execute(
            "SELECT columns_json FROM config_tables WHERE table_name=?",
            (full_table,)
        )
        meta_row = meta_cur.fetchone()
        column_defs = json.loads(meta_row["columns_json"] if meta_row else "[]")

        # 构建查询
        col_names = [c["name"] for c in column_defs]
        # 前端期望 columns 是字符串数组（按列名取值 row[col]、渲染表头）
        columns = col_names
        base_sql = f"SELECT {','.join(col_names)} FROM {full_table}"
        count_sql = f"SELECT COUNT(*) as total FROM {full_table}"

        if keyword:
            # 模糊搜索所有文本列
            like_clauses = [f"{col} LIKE ?" for col in col_names]
            where_sql = f"WHERE {' OR '.join(like_clauses)}"
            params = [f"%{keyword}%" for _ in col_names]
            base_sql = f"{base_sql} {where_sql}"
            count_sql = f"{count_sql} {where_sql}"
        else:
            params = []

        # 查询总数
        total_cur = conn.execute(count_sql, params)
        total = total_cur.fetchone()["total"]

        # 分页查询
        data_cur = conn.execute(
            f"{base_sql} LIMIT ? OFFSET ?",
            params + [page_size, offset]
        )
        rows = [dict(r) for r in data_cur.fetchall()]

        return {
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "columns": columns,
            # 并行返回列元信息（含中文 desc），前端用来渲染双行表头
            "column_meta": column_defs,
        }


def list_config_tables() -> List[Dict[str, Any]]:
    """列出所有已导入的配置表"""
    with _connect() as conn:
        cur = conn.execute(
            """SELECT table_name, row_count, last_imported, source_file, columns_json
               FROM config_tables ORDER BY last_imported DESC"""
        )
        result = []
        for r in cur.fetchall():
            item = dict(r)
            # 从 columns_json 中解析 column_count (前端需要)
            try:
                cols = json.loads(item.pop("columns_json") or "[]")
                item["column_count"] = len(cols)
            except (json.JSONDecodeError, TypeError):
                item["column_count"] = 0
            result.append(item)
        return result


def get_import_history(limit: int = 100) -> List[Dict[str, Any]]:
    """获取导入历史记录"""
    with _connect() as conn:
        cur = conn.execute(
            """SELECT id, filename, sheet_name, row_count, imported_at, status, error_msg
               FROM config_imports ORDER BY imported_at DESC LIMIT ?""",
            (limit,)
        )
        return [dict(r) for r in cur.fetchall()]


def delete_config_table(table_name: str) -> bool:
    """删除配置表及其元信息"""
    full_table = f"cfg_{table_name}"
    with _connect() as conn:
        conn.execute("BEGIN")
        try:
            # 删除数据表
            conn.execute(f"DROP TABLE IF EXISTS {full_table}")
            # 删除元信息
            conn.execute("DELETE FROM config_tables WHERE table_name=?", (full_table,))
            # 删除相关导入历史
            conn.execute("DELETE FROM config_imports WHERE sheet_name=?", (table_name,))
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            return False


# ──────────────────────────────────────────────
# 列配置管理（Web界面管理支持）
# ──────────────────────────────────────────────

def get_column_config(sheet_name: str) -> List[Dict[str, Any]]:
    """获取指定sheet的列配置"""
    with _connect() as conn:
        cur = conn.execute(
            """SELECT id, sheet_name, column_src, column_dst, data_type, is_pk, is_enabled, display_order
               FROM column_config WHERE sheet_name=? ORDER BY display_order""",
            (sheet_name,)
        )
        return [dict(r) for r in cur.fetchall()]


def save_column_config(sheet_name: str, columns: List[Dict[str, Any]]) -> int:
    """
    保存列配置（Web界面管理用）

    Args:
        sheet_name: sheet名
        columns: 列配置列表 [{"column_src": ..., "column_dst": ..., ...}, ...]

    Returns:
        保存的列数
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute("BEGIN")
        try:
            # 先删除旧配置
            conn.execute("DELETE FROM column_config WHERE sheet_name=?", (sheet_name,))

            # 插入新配置
            for i, col in enumerate(columns):
                conn.execute(
                    """INSERT INTO column_config
                       (sheet_name, column_src, column_dst, data_type, is_pk, is_enabled, display_order, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        sheet_name,
                        col.get("column_src", ""),
                        col.get("column_dst", ""),
                        col.get("data_type", "TEXT"),
                        1 if col.get("is_pk") else 0,
                        1 if col.get("is_enabled", True) else 0,
                        i,
                        now,
                        now,
                    )
                )

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return len(columns)


def list_sheet_configs() -> List[Dict[str, Any]]:
    """列出所有sheet的列配置概览"""
    with _connect() as conn:
        cur = conn.execute(
            """SELECT sheet_name, COUNT(*) as column_count,
               SUM(is_enabled) as enabled_count,
               MAX(updated_at) as last_updated
               FROM column_config GROUP BY sheet_name ORDER BY sheet_name"""
        )
        return [dict(r) for r in cur.fetchall()]


def get_all_column_configs() -> Dict[str, List[Dict[str, Any]]]:
    """获取所有sheet的列配置（按sheet分组）

    Returns:
        {sheet_name: [col_config_list, ...}
    """
    with _connect() as conn:
        cur = conn.execute(
            """SELECT id, sheet_name, column_src, column_dst, data_type, is_pk, is_enabled, display_order
               FROM column_config ORDER BY sheet_name, display_order"""
        )
        result: Dict[str, List[Dict[str, Any]]] = {}
        for row in cur.fetchall():
            d = dict(row)
            sheet = d["sheet_name"]
            if sheet not in result:
                result[sheet] = []
            result[sheet].append(d)
        return result


def delete_sheet_config(sheet_name: str) -> bool:
    """删除sheet的列配置"""
    with _connect() as conn:
        conn.execute("DELETE FROM column_config WHERE sheet_name=?", (sheet_name,))
        return True
