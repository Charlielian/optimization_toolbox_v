"""运行时许可校验。"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import socket
import struct
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
_LICENSE_PREFIX = "wybx-license-v1|"
_CLOCK_STATE_PREFIX = "wybx-clock-v1|"
_DEFAULT_HMAC_KEY = b"wybx-dev-change-before-release-2026"
logger = logging.getLogger(__name__)


class LicenseError(Exception):
    """授权无效或已过期。"""


@dataclass(frozen=True)
class LicenseStatus:
    valid: bool
    expires: Optional[date]
    message: str
    enforced: bool


@dataclass(frozen=True)
class ClockGuardOptions:
    """防时钟篡改选项（由 config.license 解析）。"""

    state_file: str = "data/.license_clock"
    allow_rollback_days: int = 1
    use_network_time: bool = True
    max_local_drift_days: int = 2
    network_timeout_sec: float = 2.5
    ntp_servers: Tuple[str, ...] = ("ntp.aliyun.com", "cn.pool.ntp.org", "pool.ntp.org")
    http_time_urls: Tuple[str, ...] = ("https://www.baidu.com",)


def _license_hmac_key() -> bytes:
    env = os.environ.get("WYBX_LICENSE_HMAC_KEY", "").strip()
    if env:
        return env.encode("utf-8")
    key_file = ROOT_DIR / ".license_key"
    if key_file.is_file():
        text = key_file.read_text(encoding="utf-8").strip()
        if text:
            return text.encode("utf-8")
    return _DEFAULT_HMAC_KEY


def sign_expires(expires: str, key: Optional[bytes] = None) -> str:
    """根据过期日生成 HMAC 签名（供签发脚本使用）。"""
    k = key if key is not None else _license_hmac_key()
    msg = f"{_LICENSE_PREFIX}expires={expires}"
    return hmac.new(k, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _sign_clock_payload(last_utc_date: str, license_expires: str) -> str:
    msg = f"{_CLOCK_STATE_PREFIX}last={last_utc_date}|exp={license_expires}"
    return hmac.new(_license_hmac_key(), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _parse_expires(raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as e:
        raise LicenseError("软件无法启动，请联系管理员。") from e


def _load_license_file(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise LicenseError("软件无法启动，请联系管理员。")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LicenseError("软件无法启动，请联系管理员。") from e
    if not isinstance(data, dict):
        raise LicenseError("软件无法启动，请联系管理员。")
    return data


def _resolve_path(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    return p if p.is_absolute() else ROOT_DIR / p


def _ntp_utc_date(server: str, timeout: float) -> Optional[date]:
    """单次 NTP 查询，返回 UTC 日期。"""
    try:
        packet = b"\x1b" + 47 * b"\0"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(packet, (server, 123))
            data, _ = sock.recvfrom(48)
        if len(data) < 48:
            return None
        # transmit timestamp (bytes 40–47): seconds since 1900-01-01
        t, frac = struct.unpack("!II", data[40:48])
        ntp = t + frac / 2**32 - 2208988800  # to Unix epoch
        return datetime.fromtimestamp(ntp, tz=timezone.utc).date()
    except OSError:
        return None


def _http_utc_date(url: str, timeout: float) -> Optional[date]:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.headers.get("Date")
        if not raw:
            return None
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date()
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return None


def fetch_network_utc_date(opts: ClockGuardOptions) -> Optional[date]:
    """尽力从 NTP 或 HTTP Date 获取 UTC 日期；全部失败返回 None。"""
    per = max(0.8, opts.network_timeout_sec / max(len(opts.ntp_servers), 1))
    for host in opts.ntp_servers:
        d = _ntp_utc_date(host, per)
        if d is not None:
            return d
    for url in opts.http_time_urls:
        d = _http_utc_date(url, opts.network_timeout_sec)
        if d is not None:
            return d
    return None


def _read_clock_state(path: Path, license_expires: date) -> Optional[date]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise LicenseError("软件无法启动，请联系管理员。") from None
    if not isinstance(data, dict):
        raise LicenseError("软件无法启动，请联系管理员。")
    last_s = data.get("last_utc_date")
    exp_s = data.get("license_expires")
    sig = data.get("signature")
    if not last_s or not exp_s or not sig:
        return None
    if str(exp_s) != license_expires.isoformat():
        return None
    expected = _sign_clock_payload(str(last_s), str(exp_s))
    if not hmac.compare_digest(str(sig).lower(), expected.lower()):
        raise LicenseError("软件无法启动，请联系管理员。")
    try:
        return date.fromisoformat(str(last_s))
    except ValueError:
        raise LicenseError("软件无法启动，请联系管理员。") from None


def _write_clock_state(path: Path, last_utc: date, license_expires: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exp_s = license_expires.isoformat()
    last_s = last_utc.isoformat()
    payload = {
        "last_utc_date": last_s,
        "license_expires": exp_s,
        "signature": _sign_clock_payload(last_s, exp_s),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def resolve_trusted_utc_date(
    license_expires: date,
    opts: ClockGuardOptions,
    *,
    local_utc: Optional[date] = None,
) -> date:
    """
    得到用于比对 expires 的「可信 UTC 日期」。
    检测：回拨、状态篡改、（可选）与网络时间偏差。
    """
    local = local_utc or datetime.now(timezone.utc).date()
    state_path = _resolve_path(opts.state_file)

    recorded = _read_clock_state(state_path, license_expires)
    if recorded is not None:
        # 允许少量回拨（时区/夏令时误操作），超过则视为改系统时间
        if local < recorded - timedelta(days=opts.allow_rollback_days):
            raise LicenseError("软件无法启动，请检查系统时间是否正确。")

    network = None
    if opts.use_network_time:
        network = fetch_network_utc_date(opts)
        if network is not None:
            drift = abs((local - network).days)
            if drift > opts.max_local_drift_days:
                raise LicenseError("软件无法启动，请检查系统时间是否正确。")
            if local < network - timedelta(days=opts.allow_rollback_days):
                raise LicenseError("软件无法启动，请检查系统时间是否正确。")

    # 取较「新」的日期做过期判断，避免只靠回拨；与 network 取 max 更严
    candidates: List[date] = [local]
    if recorded is not None:
        candidates.append(recorded)
    if network is not None:
        candidates.append(network)
    trusted = max(candidates)

    if trusted > license_expires:
        raise LicenseError("软件无法使用，请联系管理员。")

    # 仅在校验通过后推进状态（防止用户删文件反复回拨：删文件后仍受 network 约束）
    advance = trusted
    if recorded is not None and advance < recorded:
        advance = recorded
    _write_clock_state(state_path, advance, license_expires)
    return trusted


def verify_license_file(
    license_path: Path,
    *,
    clock: Optional[ClockGuardOptions] = None,
    today: Optional[date] = None,
) -> Tuple[date, str]:
    data = _load_license_file(license_path)
    expires_raw = data.get("expires")
    signature = data.get("signature")
    if not expires_raw or not signature:
        raise LicenseError("软件无法启动，请联系管理员。")

    expires = _parse_expires(str(expires_raw))
    expected = sign_expires(expires.isoformat())
    if not hmac.compare_digest(str(signature).lower(), expected.lower()):
        raise LicenseError("软件无法启动，请联系管理员。")

    if today is not None:
        ref = today
        if ref > expires:
            raise LicenseError("软件无法使用，请联系管理员。")
    elif clock is not None:
        resolve_trusted_utc_date(expires, clock)
    else:
        ref = datetime.now(timezone.utc).date()
        if ref > expires:
            raise LicenseError("软件无法使用，请联系管理员。")

    return expires, "ok"


def clock_guard_from_config(cfg: Dict[str, Any]) -> ClockGuardOptions:
    lic = cfg.get("license") or {}
    cg = lic.get("clock_guard") or {}
    ntp = cg.get("ntp_servers")
    if isinstance(ntp, list) and ntp:
        ntp_t = tuple(str(x) for x in ntp)
    else:
        ntp_t = ClockGuardOptions.ntp_servers
    http_u = cg.get("http_time_urls")
    if isinstance(http_u, list) and http_u:
        http_t = tuple(str(x) for x in http_u)
    else:
        http_t = ClockGuardOptions.http_time_urls
    return ClockGuardOptions(
        state_file=str(cg.get("state_file", "data/.license_clock")),
        allow_rollback_days=int(cg.get("allow_rollback_days", 1)),
        use_network_time=bool(cg.get("use_network_time", True)),
        max_local_drift_days=int(cg.get("max_local_drift_days", 2)),
        network_timeout_sec=float(cg.get("network_timeout_sec", 2.5)),
        ntp_servers=ntp_t,
        http_time_urls=http_t,
    )


def check_license(
    enabled: bool,
    license_file: str,
    *,
    clock: Optional[ClockGuardOptions] = None,
    today: Optional[date] = None,
) -> LicenseStatus:
    if not enabled:
        return LicenseStatus(
            valid=True,
            expires=None,
            message="",
            enforced=False,
        )

    path = Path(license_file)
    if not path.is_absolute():
        path = ROOT_DIR / path

    try:
        expires, _msg = verify_license_file(path, clock=clock, today=today)
        return LicenseStatus(valid=True, expires=expires, message="", enforced=True)
    except LicenseError as e:
        return LicenseStatus(valid=False, expires=None, message=str(e), enforced=True)


def ensure_license_or_exit(
    enabled: bool,
    license_file: str,
    *,
    clock: Optional[ClockGuardOptions] = None,
) -> LicenseStatus:
    status = check_license(enabled, license_file, clock=clock)
    if status.enforced and not status.valid:
        print(status.message, file=sys.stderr)
        sys.exit(1)
    return status


def expired_html(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>网优百宝箱</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #f5f5f5; margin: 0; padding: 2rem; }}
    .box {{ max-width: 520px; margin: 4rem auto; background: #fff; padding: 2rem; border-radius: 8px;
            box-shadow: 0 2px 12px rgba(0,0,0,.08); }}
    h1 {{ color: #c62828; font-size: 1.35rem; margin-top: 0; }}
    p {{ color: #444; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="box">
    <h1>无法使用本软件</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""