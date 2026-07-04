"""
配置导入配置读取模块

从 config.yaml 的 import_configs 节点读取配置，
同时支持从数据库读取配置（数据库配置优先），
提供查询接口：启用的sheet列表、列映射等。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

__all__ = [
    "load_import_config",
    "get_enabled_sheets",
    "get_sheet_config",
    "get_sheet_columns",
    "get_config_dir",
    "get_sheet_description",
    "is_sheet_enabled",
    "list_all_config_sheets",
    "save_sheet_config_to_yaml",
    "reload_config",
]

# 项目根目录
ROOT_DIR = Path(__file__).parent.parent
CONFIG_FILE = ROOT_DIR / "config.yaml"

# 缓存配置，避免重复读取
_config_cache: Optional[Dict[str, Any]] = None


def load_import_config() -> Dict[str, Any]:
    """加载 import_configs 配置段"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if not CONFIG_FILE.exists():
        _config_cache = {}
        return _config_cache

    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        _config_cache = cfg.get("import_configs", {})
        return _config_cache
    except Exception:
        _config_cache = {}
        return _config_cache


def reload_config() -> None:
    """重新加载配置（配置文件修改后调用）"""
    global _config_cache
    _config_cache = None
    load_import_config()


def get_config_dir() -> Path:
    """获取配置文件目录路径"""
    cfg = load_import_config()
    subdir = cfg.get("config_dir", "网管文件")
    return ROOT_DIR / subdir


def _get_db_config_map() -> Dict[str, Dict[str, Any]]:
    """
    从数据库获取所有sheet的配置，并转换为YAML格式的字典

    注意: 数据库配置已被禁用, 此函数仅保留作为兼容入口, 始终返回空字典。
    配置以 config.yaml 为唯一来源。

    Returns:
        {} (数据库覆盖逻辑已关闭)
    """
    return {}


def _get_merged_config() -> Dict[str, Any]:
    """
    获取合并后的配置 (仅 YAML, 数据库覆盖逻辑已关闭)

    历史上: 数据库配置优先于YAML配置。
    现在: config.yaml 是 import_configs 的唯一来源, 数据库表 column_config 不再参与。

    Returns:
        load_import_config() 的原始结果
    """
    return load_import_config()


def get_enabled_sheets() -> List[str]:
    """获取启用的sheet名称列表（仅从 config.yaml 读取）"""
    cfg = _get_merged_config()
    sheets = cfg.get("sheets", {})
    return [name for name, conf in sheets.items() if conf.get("enabled", False)]


def get_sheet_config(sheet_name: str) -> Dict[str, Any]:
    """获取指定sheet的完整配置（仅从 config.yaml 读取）"""
    cfg = _get_merged_config()
    sheets = cfg.get("sheets", {})
    return sheets.get(sheet_name, {})


def get_sheet_columns(sheet_name: str) -> Dict[str, str]:
    """
    获取指定sheet的列映射配置（仅从 config.yaml 读取）

    Returns:
        Dict[str, str]: {Excel列名: 数据库列名}
    """
    sheet_cfg = get_sheet_config(sheet_name)
    return sheet_cfg.get("columns", {})


def get_sheet_description(sheet_name: str) -> str:
    """获取指定sheet的描述（仅从 config.yaml 读取）"""
    sheet_cfg = get_sheet_config(sheet_name)
    return sheet_cfg.get("description", "")


def is_sheet_enabled(sheet_name: str) -> bool:
    """检查指定sheet是否启用（仅从 config.yaml 读取）"""
    sheet_cfg = get_sheet_config(sheet_name)
    return sheet_cfg.get("enabled", False)


def list_all_config_sheets() -> List[Dict[str, Any]]:
    """
    列出所有配置的sheet信息（包括未启用的）（仅从 config.yaml 读取）

    Returns:
        [{"name": ..., "enabled": ..., "description": ..., "column_count": ...}, ...]
    """
    cfg = _get_merged_config()
    sheets = cfg.get("sheets", {})
    result = []
    for name, conf in sheets.items():
        result.append({
            "name": name,
            "enabled": conf.get("enabled", False),
            "description": conf.get("description", ""),
            "column_count": len(conf.get("columns", {})),
        })
    return result


def save_sheet_config_to_yaml(sheet_name: str, columns: List[Dict[str, Any]], description: str = "", enabled: bool = True) -> bool:
    """
    保存sheet的列配置到YAML文件

    Args:
        sheet_name: sheet名称
        columns: 列配置列表，每项包含 column_src, column_dst, is_enabled, data_type, is_pk
        description: 描述
        enabled: 是否启用

    Returns:
        True成功
    """
    global _config_cache

    try:
        # 读取当前完整配置
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, encoding="utf-8") as f:
                full_cfg = yaml.safe_load(f) or {}
        else:
            full_cfg = {}

        # 确保 import_configs 节点存在
        if "import_configs" not in full_cfg:
            full_cfg["import_configs"] = {}
        if "sheets" not in full_cfg["import_configs"]:
            full_cfg["import_configs"]["sheets"] = {}

        sheets_cfg = full_cfg["import_configs"]["sheets"]

        # 构建列映射: 只包含启用的列, Excel列名 -> 数据库列名
        col_map = {}
        for col in columns:
            if col.get("is_enabled", True):
                col_map[col["column_src"]] = col["column_dst"]

        # 更新或新增sheet配置
        if sheet_name in sheets_cfg:
            sheets_cfg[sheet_name]["enabled"] = enabled
            if description:
                sheets_cfg[sheet_name]["description"] = description
            sheets_cfg[sheet_name]["columns"] = col_map
        else:
            sheets_cfg[sheet_name] = {
                "enabled": enabled,
                "description": description or sheet_name,
                "columns": col_map,
            }

        # 写回YAML文件
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        # 清除缓存
        _config_cache = None

        return True
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"保存YAML配置失败: {e}")
        return False