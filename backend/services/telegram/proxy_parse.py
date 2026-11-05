"""代理线路粘贴 / 订阅解析（fork 新增）。

与 telegram-manager 的解析口径一致，并补上 demo 面板粘贴页的格式：
  * tg://proxy?server=&port=&secret=、t.me/proxy?、telegram.me/proxy?
    → mtproto
  * t.me/socks?server=&port=&user=&pass= → socks5
  * https:// → http；http/socks5/socks4://[user:pass@]host:port
  * 裸 1.2.3.4:1080（默认 SOCKS5）、1.2.3.4:1080:user:pass、
    IP:PORT:USER:PASS、IP:PORT@USER:PASS
  * socks://BASE64（base64 解出 user:pass@host:port 或 host:port）
  * 整段 base64 订阅块：整段解不开行时先整体 base64 解码再按行解析
去重键：mtproto 按 (ptype, host, port, secret)，其余按 (ptype, host, port)。
解析失败的行直接跳过（不抛错），调用方按 added/skipped 计数汇报。
"""

from __future__ import annotations

import base64
import binascii
import re
import urllib.parse
from typing import Any, Optional

_PROXY_TYPES = ("http", "https", "socks5", "socks4")
_MTPROXY_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t(?:elegram)?\.(?:org|me|dog)/proxy\?|tg://proxy\?)(.+)",
    re.IGNORECASE,
)
_SOCKS_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t(?:elegram)?\.(?:org|me|dog)/socks\?|tg://socks\?)(.+)",
    re.IGNORECASE,
)
_BARE_IP_PORT_RE = re.compile(
    r"^(?P<host>[^:\s/@]+):(?P<port>\d{1,5})"
    r"(?::(?P<user>[^:\s/@]+):(?P<pass>[^:\s]+))?$"
)
_BARE_IP_PORT_AT_RE = re.compile(
    r"^(?P<host>[^:\s/@]+):(?P<port>\d{1,5})@(?P<user>[^:\s]+):(?P<pass>[^:\s]+)$"
)
_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _valid_port(port: Any) -> Optional[int]:
    try:
        p = int(port)
    except (TypeError, ValueError):
        return None
    return p if 1 <= p <= 65535 else None


def _valid_host(host: Any) -> Optional[str]:
    if not host or not isinstance(host, str):
        return None
    host = host.strip().lower()
    if not host or len(host) > 255 or not _HOST_RE.match(host):
        return None
    return host


def _dedup_key(entry: dict) -> tuple:
    if entry["ptype"] == "mtproto":
        return (
            "mtproto",
            entry["host"],
            entry["port"],
            entry.get("secret") or "",
        )
    return (entry["ptype"], entry["host"], entry["port"])


def _entry(
    ptype: str,
    host: str,
    port: int,
    username: Optional[str] = None,
    password: Optional[str] = None,
    secret: Optional[str] = None,
) -> dict:
    return {
        "ptype": ptype,
        "host": host,
        "port": port,
        "username": username or None,
        "password": password or None,
        "secret": secret or None,
    }


def _parse_mtproxy_link(value: str) -> Optional[dict]:
    match = _MTPROXY_LINK_RE.match(value.strip())
    if not match:
        return None
    params = urllib.parse.parse_qs(match.group(1))
    host = _valid_host((params.get("server") or [None])[0])
    port = _valid_port((params.get("port") or [None])[0])
    secret = (params.get("secret") or [None])[0]
    if not host or not port or not secret:
        return None
    return _entry("mtproto", host, port, secret=secret.strip())


def _parse_socks_link(value: str) -> Optional[dict]:
    match = _SOCKS_LINK_RE.match(value.strip())
    if not match:
        return None
    params = urllib.parse.parse_qs(match.group(1))
    host = _valid_host((params.get("server") or [None])[0])
    port = _valid_port((params.get("port") or [None])[0])
    if not host or not port:
        return None
    user = (params.get("user") or [None])[0]
    password = (params.get("pass") or [None])[0]
    return _entry("socks5", host, port, username=user, password=password)


def _parse_base64_socks(value: str) -> Optional[dict]:
    """socks://BASE64，解出 user:pass@host:port 或 host:port。"""
    if not value.lower().startswith("socks://"):
        return None
    payload = value[len("socks://") :].strip()
    if not payload:
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode(
            "utf-8", errors="strict"
        )
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    decoded = decoded.strip()
    if "://" in decoded:
        return _parse_line(decoded)
    if "@" in decoded:
        auth, _, hostport = decoded.rpartition("@")
        user, _, password = auth.partition(":")
        if ":" not in hostport:
            return None
        host, _, port = hostport.rpartition(":")
        host = _valid_host(host)
        port_i = _valid_port(port)
        if not host or not port_i:
            return None
        return _entry(
            "socks5", host, port_i, username=user or None, password=password or None
        )
    return _parse_line(decoded)


def _parse_url_line(value: str) -> Optional[dict]:
    try:
        parsed = urllib.parse.urlparse(value.strip())
    except Exception:
        return None
    scheme = (parsed.scheme or "").lower()
    if scheme == "tg":
        return None  # tg:// 链接走 _parse_mtproxy_link / _parse_socks_link
    if scheme == "https":
        scheme = "http"
    if scheme not in _PROXY_TYPES:
        return None
    host = _valid_host(parsed.hostname)
    port = _valid_port(parsed.port) if parsed.port else None
    if not host or not port:
        return None
    try:
        username = urllib.parse.unquote(parsed.username) if parsed.username else None
        password = urllib.parse.unquote(parsed.password) if parsed.password else None
    except Exception:
        username = password = None
    return _entry(scheme, host, port, username=username, password=password)


def _parse_bare_line(value: str) -> Optional[dict]:
    match = _BARE_IP_PORT_AT_RE.match(value.strip())
    if match:
        host = _valid_host(match.group("host"))
        port = _valid_port(match.group("port"))
        if not host or not port:
            return None
        return _entry(
            "socks5",
            host,
            port,
            username=match.group("user"),
            password=match.group("pass"),
        )
    match = _BARE_IP_PORT_RE.match(value.strip())
    if match:
        host = _valid_host(match.group("host"))
        port = _valid_port(match.group("port"))
        if not host or not port:
            return None
        return _entry(
            "socks5",
            host,
            port,
            username=match.group("user"),
            password=match.group("pass"),
        )
    return None


def _parse_line(line: str) -> Optional[dict]:
    value = (line or "").strip()
    if not value or value.startswith("#"):
        return None
    parsed = _parse_mtproxy_link(value)
    if parsed:
        return parsed
    parsed = _parse_socks_link(value)
    if parsed:
        return parsed
    parsed = _parse_base64_socks(value)
    if parsed:
        return parsed
    if "://" in value:
        return _parse_url_line(value)
    return _parse_bare_line(value)


def _maybe_decode_subscription_block(text: str) -> str:
    """整段 base64 订阅块：整段去掉空白后能 base64 解出多行代理时用解码结果。"""
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) < 16:
        return text
    try:
        padded = compact + "=" * (-len(compact) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode(
            "utf-8", errors="strict"
        )
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return text
    if "://" in decoded or re.search(r":\d{1,5}", decoded):
        lines = [ln for ln in decoded.splitlines() if ln.strip()]
        if len(lines) >= 1 and sum(1 for ln in lines if _parse_line(ln)) >= 1:
            return decoded
    return text


def parse_proxy_lines(text: str) -> list[dict]:
    """解析粘贴的代理线路（每行一条），返回去重后的条目列表。"""
    text = _maybe_decode_subscription_block(text or "")
    out: list[dict] = []
    seen: set[tuple] = set()
    for raw in text.splitlines():
        entry = _parse_line(raw)
        if entry is None:
            continue
        key = _dedup_key(entry)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def parse_subscription_text(text: str) -> list[dict]:
    """解析订阅返回文本（与粘贴解析同一套格式）。"""
    return parse_proxy_lines(text)
