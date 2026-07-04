"""
一次性回填脚本: 统一以 name 后缀为权威标识, 修正 cells 中的
  manufacturer (厂家)  和  oms_name (归属网管)

规则 (与 sector_params._derive_oms_name 一致):
  - name 含 -NLH-/-NLW-/-NLO-   → 诺基亚
  - name 含 RGS-                → 中兴 (700M)
  - name 含 -SM-/-GZ-/RD-/RDC-, 或 Z 系列 (ZFH/ZLR/ZRW/ZRH/ZNH/ZLH/ZLW/ZFW/Z5H)
                                → 中兴 (2.6G)

当反推结果与原 manufacturer 不一致时, 以 name 为准。

用法:
    cd backend && python backfill_oms_name.py
"""
import sys
import re
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sector_params import _derive_oms_name
from db import _connect


_NAME_MF_PATTERN = re.compile(r"-([A-Z0-9]+)-\d+$")
_CBN_PATTERN = re.compile(r"CBN-", re.IGNORECASE)

# 名称后缀 → 厂家
_SUFFIX_TO_MF = {
    # 诺基亚
    "NLH": "诺基亚", "NLW": "诺基亚", "NLO": "诺基亚",
    # 中兴 (2.6G)
    "ZFH": "中兴", "ZLR": "中兴", "ZRW": "中兴", "ZRH": "中兴",
    "ZNH": "中兴", "ZLH": "中兴", "ZLW": "中兴", "ZFW": "中兴",
    "Z5H": "中兴",
}


def _infer_manufacturer_from_name(name: str) -> Tuple[str, str]:
    """
    返回 (suffix, mf); 推断失败则 suffix='', mf=''
    CBN- 前缀 → 中兴 (700M 业务汇聚)
    """
    if not name:
        return "", ""
    if _CBN_PATTERN.search(name):
        return "CBN", "中兴"
    m = _NAME_MF_PATTERN.search(name.upper())
    if not m:
        return "", ""
    suffix = m.group(1)
    return suffix, _SUFFIX_TO_MF.get(suffix, "")


def main() -> None:
    conn = _connect()
    try:
        # 全表扫描, 不只 oms_name='未知'
        rows = conn.execute(
            """
            SELECT ecgi, name, manufacturer, freq_band, oms_name
            FROM cells
            """
        ).fetchall()

        if not rows:
            print("[OK] cells 表为空")
            return

        print(f"[INFO] 扫描 {len(rows)} 条 cells")

        mf_fixed = 0           # manufacturer 被修正
        mf_added = 0           # manufacturer 从空补全
        oms_updated = 0        # oms_name 被更新
        oms_unchanged = 0
        oms_unknown = 0

        sample_logs = []

        for r in rows:
            name = r["name"] or ""
            old_mf = (r["manufacturer"] or "").strip()
            freq_band = (r["freq_band"] or "").strip()
            old_oms = r["oms_name"] or ""

            suffix, inferred_mf = _infer_manufacturer_from_name(name)

            # 1) 修正 manufacturer
            mf_changed = False
            new_mf = old_mf
            if inferred_mf:
                if not old_mf:
                    new_mf = inferred_mf
                    mf_added += 1
                    mf_changed = True
                elif old_mf != inferred_mf:
                    new_mf = inferred_mf
                    mf_fixed += 1
                    mf_changed = True

            # 2) 派生 oms_name (按新规则, name 后缀优先)
            new_oms = _derive_oms_name(new_mf, freq_band, name)

            update_fields = []
            update_values = []
            if mf_changed:
                update_fields.append("manufacturer = ?")
                update_values.append(new_mf)
            if new_oms != old_oms:
                update_fields.append("oms_name = ?")
                update_values.append(new_oms)

            if update_fields:
                update_values.append(r["ecgi"])
                conn.execute(
                    f"UPDATE cells SET {', '.join(update_fields)} WHERE ecgi = ?",
                    update_values,
                )
                if new_oms != old_oms:
                    oms_updated += 1
                marker = "*MF*" if mf_changed else "    "
                if len(sample_logs) < 60 and (mf_changed or new_oms != old_oms):
                    sample_logs.append(
                        f"  {marker} ecgi={r['ecgi']:<16} name={(name[:32] + '…') if len(name) > 32 else name!r:<35}  "
                        f"mf {old_mf!r:>6} → {new_mf!r:<6}  oms {old_oms!r:>10} → {new_oms!r}"
                    )
            else:
                oms_unchanged += 1

            if new_oms == "未知":
                oms_unknown += 1

        # 输出
        print("\n".join(sample_logs))
        print(
            f"\n[OK] 完成: 反推厂家 {mf_added + mf_fixed} 条 (新增 {mf_added} / 修正 {mf_fixed}), "
            f"oms 更新 {oms_updated} 条, 未变 {oms_unchanged} 条, 仍为'未知' {oms_unknown} 条"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()