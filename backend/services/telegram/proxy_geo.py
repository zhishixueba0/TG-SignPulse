"""代理归属地查询与地区命名（fork 新增）。

口径与 telegram-manager 一致（2026-11-05 用户纠正）：
地区永远看**宣告地区**（BGP geofeed），不是 IP 注册地。
  1. ipinfo：宣告/实际使用地（吃 geofeed，宣告权重高），顺带拿 org 与 ASN
  2. RIPEstat network-info：当前 BGP 宣告这个 IP 的 ASN 与前缀（权威路由事实）
  3. ip-api（http，免费版只走 http）：注册国家 + ISP/机房
  4. ipwho.is 兜底
两国不一致时注册国家单独存 reg_country；命名/分组/中伊剔除只用宣告国家。
查不到就返回 None，不阻塞添加流程。
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from typing import Any, Callable, Optional

logger = logging.getLogger("backend.proxy_geo")

# 自动剔除的地区：中国大陆和伊朗的代理不需要
BLOCKED_PROXY_COUNTRIES = {"CN", "IR"}

_IPINFO_URL = "https://ipinfo.io/{ip}/json"
_RIPESTAT_URL = "https://stat.ripe.net/data/network-info/data.json?resource={ip}"
_IPAPI_URL = "http://ip-api.com/json/{ip}?fields=status,countryCode,isp,org,hosting"
_IPWHOIS_URL = "https://ipwho.is/{ip}"

# 命名格式：旗帜-地区-IP类型，如 🇺🇸-US-ISP、🇩🇪-DE-机房、🇯🇵-JP-ISP
_FLAG_OFFSET = 0x1F1E6


def _flag_emoji(code: Optional[str]) -> str:
    c = (code or "").upper()
    if len(c) != 2 or not c.isascii() or not c.isalpha():
        return ""
    return "".join(chr(_FLAG_OFFSET + ord(ch) - 65) for ch in c)


def is_blocked_country(code: Optional[str]) -> bool:
    return bool(code) and code.upper() in BLOCKED_PROXY_COUNTRIES


def is_blocked_geo(geo: Optional[dict]) -> bool:
    """宣告地或注册地任一是中国大陆/伊朗就判为 blocked。"""
    if not geo:
        return False
    return is_blocked_country(geo.get("country_code")) or is_blocked_country(
        geo.get("reg_country")
    )


def label_blocked_code(label: Optional[str]) -> Optional[str]:
    """系统自动名里带的地区代码可信，直接按它判中伊。"""
    if not label:
        return None
    match = re.match(r"^[\U0001F1E6-\U0001F1FF]{2}-([A-Z]{2})-(ISP|机房|未知)", label)
    if match and match.group(1) in BLOCKED_PROXY_COUNTRIES:
        return match.group(1)
    match = re.match(r"^([A-Z]{2})-\d+$", label)
    if match and match.group(1) in BLOCKED_PROXY_COUNTRIES:
        return match.group(1)
    return None


def is_auto_label(label: Optional[str]) -> bool:
    """判断一个名字是不是系统自动起的（自动的才会在补全时被重命名）。"""
    if not label:
        return True
    if label.startswith("其他-") or label.startswith("自动 ") or label == "待命名":
        return True
    if re.match(r"^[A-Z]{2}-\d+$", label):
        return True
    if re.match(r"^[\U0001F1E6-\U0001F1FF]{2}-[A-Z]{2}-(ISP|机房|未知)(-\d+)?$", label):
        return True
    return False


def region_name(
    country_code: Optional[str],
    is_hosting: Optional[bool] = None,
    existing_labels: Optional[list[str]] = None,
    exclude_label: Optional[str] = None,
) -> str:
    """自动命名：旗帜-地区-IP类型；重复才加 -02、-03 序号。"""
    code = (country_code or "其他").upper()
    ip_type = "机房" if is_hosting is True else "ISP" if is_hosting is False else "未知"
    flag = _flag_emoji(code)
    base = f"{flag}-{code}-{ip_type}" if flag else f"{code}-{ip_type}"
    labels = [lbl for lbl in (existing_labels or []) if lbl != exclude_label]
    if base not in labels:
        return base
    n = 2
    while f"{base}-{n:02d}" in labels:
        n += 1
    return f"{base}-{n:02d}"


def ip_type_label(is_hosting: Optional[bool]) -> str:
    return "机房" if is_hosting is True else "ISP" if is_hosting is False else "未知"


async def _resolve_ip(host: str) -> Optional[str]:
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        return await asyncio.to_thread(socket.gethostbyname, host)
    except Exception:
        return None


async def lookup_geo(host: str) -> Optional[dict]:
    """归属地查询：ipinfo → RIPEstat → ip-api → ipwho.is，全部失败返回 None。"""
    import httpx

    ip = await _resolve_ip(host)
    if not ip:
        return None

    announced: Optional[str] = None
    reg: Optional[str] = None
    isp: Optional[str] = None
    org: Optional[str] = None
    is_hosting: Optional[bool] = None
    asn: Optional[str] = None
    prefix: Optional[str] = None

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        # 1) ipinfo：宣告/实际使用地
        try:
            resp = await client.get(
                _IPINFO_URL.format(ip=ip),
                headers={"User-Agent": "tg-signpulse"},
            )
            data = resp.json()
            if isinstance(data, dict):
                if data.get("country"):
                    announced = str(data["country"]).upper()
                if data.get("org"):
                    org = str(data["org"])
                asn_info = data.get("asn")
                if isinstance(asn_info, dict) and asn_info.get("asn"):
                    asn = str(asn_info["asn"])
        except Exception as exc:
            logger.debug("ipinfo 查询失败 %s: %s", ip, exc)
        # 2) RIPEstat：当前宣告 ASN 与前缀
        try:
            resp = await client.get(
                _RIPESTAT_URL.format(ip=ip),
                headers={"User-Agent": "tg-signpulse"},
            )
            data = resp.json()
            inner = (data or {}).get("data") or {}
            if inner.get("prefix"):
                prefix = str(inner["prefix"])
            asns = inner.get("asns") or []
            if asns:
                asn = asn or str(asns[0])
        except Exception as exc:
            logger.debug("RIPEstat 查询失败 %s: %s", ip, exc)
        # 3) ip-api（http）：注册国家 + ISP/机房
        try:
            resp = await client.get(
                _IPAPI_URL.format(ip=ip),
                headers={"User-Agent": "tg-signpulse"},
                timeout=5.0,
            )
            data = resp.json()
            if isinstance(data, dict) and data.get("status") == "success":
                if data.get("countryCode"):
                    reg = str(data["countryCode"]).upper()
                isp = isp or data.get("isp") or None
                org = org or data.get("org") or None
                if data.get("hosting") is not None:
                    is_hosting = bool(data.get("hosting"))
        except Exception as exc:
            logger.debug("ip-api 查询失败 %s: %s", ip, exc)

    if not announced:
        announced = reg
        reg = None
    if not announced:
        # 4) ipwho.is 兜底
        try:
            import httpx as _httpx

            async with _httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(_IPWHOIS_URL.format(ip=ip))
                data = resp.json()
            if isinstance(data, dict) and data.get("success"):
                if data.get("country_code"):
                    announced = str(data["country_code"]).upper()
                conn = data.get("connection") or {}
                sec = data.get("security") or {}
                isp = isp or conn.get("isp") or None
                org = org or conn.get("org") or None
                if sec.get("hosting") is not None:
                    is_hosting = bool(sec.get("hosting"))
        except Exception as exc:
            logger.debug("ipwho.is 查询失败 %s: %s", ip, exc)

    if not announced:
        return None
    return {
        "country_code": announced,
        "reg_country": reg if reg and reg != announced else None,
        "announced_asn": asn,
        "announced_prefix": prefix,
        "isp": isp,
        "org": org,
        "is_hosting": is_hosting,
    }


# 便于测试注入的查询函数类型
GeoLookupFn = Callable[[str], Any]
