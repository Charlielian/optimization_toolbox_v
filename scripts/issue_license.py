#!/usr/bin/env python3
"""签发 license.lic（过期日期经 HMAC 签名，不可手工改日期）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from license_check import ROOT_DIR, sign_expires  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="签发网优百宝箱 license.lic")
    parser.add_argument(
        "--expires",
        required=True,
        help="授权截止日期，格式 YYYY-MM-DD",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT_DIR / "license.lic",
        help="输出路径（默认项目根目录 license.lic）",
    )
    args = parser.parse_args()

    expires = args.expires.strip()
    sig = sign_expires(expires)
    payload = {"expires": expires, "signature": sig}
    out: Path = args.out
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {out}")
    print(f"  expires:   {expires}")
    print(f"  signature: {sig[:16]}…")


if __name__ == "__main__":
    main()