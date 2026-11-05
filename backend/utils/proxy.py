from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional, Tuple
from urllib.parse import urlparse

import httpx

from backend.utils.mtproxy import (
    normalize_mtproto_secret,
    parse_mtproxy_link,
    probe_mtproxy_handshake,
    secret_cache_hash,
)
from backend.utils.proxy_handshake import (
    PROBE_HANDSHAKE_TIMEOUT_SECONDS,
    probe_telegram_handshake,
)

try:
    import socks
    from sockshandler import SocksiPyHandler
except ImportError:
    socks = None
    SocksiPyHandler = None

from backend.utils.cache import TTLCache

logger = logging.getLogger("backend.utils.proxy")

SUPPORTED_PROXY_SCHEMES = frozenset({"http", "https", "socks4", "socks5", "mtproto"})
DEFAULT_PROBE_ENDPOINTS = (
    "https://api64.ipify.org",
    "https://icanhazip.com",
)
# Plaintext fallback for http proxies that only forward cleartext and
# cannot CONNECT to https endpoints (Drive 1.0.129 note: both IP services
# serve plain http too). Only ever tried for scheme "http", after both
# https endpoints failed.
PROBE_HTTP_PLAINTEXT_ENDPOINTS = (
    "http://api64.ipify.org",
    "http://icanhazip.com",
)
# Per-endpoint timeout for the exit-IP fetch. Endpoints run in parallel,
# so a first probe costs at most ~5s here (+5s handshake screen, +5s
# direct baseline on a cold cache) -- well inside the 30s frontend cap.
PROBE_ENDPOINT_TIMEOUT_SECONDS = 5.0

# 代理探测缓存 (TTL = 600s)
_PROBE_CACHE: TTLCache[tuple[ProxyProbeStatus, Optional[str]]] = TTLCache(
    maxsize=256, ttl=600.0
)
# 宿主机直连出口 IP 缓存 (TTL = 600s)
_HOST_IP_CACHE: TTLCache[str] = TTLCache(maxsize=1, ttl=600.0)


class ProxyProbeStatus(str, Enum):
    OK = "OK"
    FAILED = "PROXY_PROBE_FAILED"  # 出口 IP 等于服务器宿主机 IP
    UNAVAILABLE = "PROXY_PROBE_UNAVAILABLE"  # 探针端点无法访问/超时


@dataclass
class ProxyProbeOutcome:
    """One probe result.

    Unpacks as a 2-tuple ``(status, detail)`` for backward compatibility
    with the gate/batch callers, and additionally carries the measured
    real-network latency and cache provenance:

    * ``latency_ms`` -- measured wall time of the real network probe
      (handshake screen + exit-IP fetch; MTProto: TCP connect time
      only, the 2.0s confirmation wait excluded). ``None`` on a cache
      hit or when no real probe ran -- a cached hit must never be
      reported as 0ms of real latency.
    * ``cached`` -- True when the result came from the TTL cache.
    * ``code`` -- fine-grained result code (handshake codes such as
      ``PROXY_AUTH_FAILED`` / ``PROXY_CONNECT_TIMEOUT``, ``OK``,
      ``PROXY_PROBE_FAILED``, ``PROXY_PROBE_UNAVAILABLE``,
      ``PROXY_CIRCUIT_BREAKER``).
    """

    status: ProxyProbeStatus
    detail: Optional[str] = None
    latency_ms: Optional[int] = None
    cached: bool = False
    code: str = "PROXY_PROBE_UNAVAILABLE"

    def __iter__(self) -> Iterator[Tuple[ProxyProbeStatus, Optional[str]]]:
        yield self.status
        yield self.detail


def clear_proxy_probe_cache() -> None:
    """清空代理探测与宿主机 IP 缓存（供测试与重载使用）。"""
    _PROBE_CACHE.clear()
    _HOST_IP_CACHE.clear()
    try:
        from backend.services.proxy_circuit_breaker import (
            get_proxy_circuit_breaker,
        )

        get_proxy_circuit_breaker().clear()
    except Exception:
        pass


def normalize_proxy_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        return value
    link = parse_mtproxy_link(value)
    if link is not None:
        return link
    if "://" in value:
        return value
    if "@" in value:
        return f"socks5://{value}"
    parts = value.split(":")
    if len(parts) == 2:
        host, port = parts
        return f"socks5://{host}:{port}"
    if len(parts) == 4:
        host, port, user, password = parts
        return f"socks5://{user}:{password}@{host}:{port}"
    return f"socks5://{value}"


def build_proxy_dict(raw: str) -> Optional[dict]:
    value = normalize_proxy_url(raw)
    if not value:
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (ValueError, AttributeError):
        return None
    scheme = (parsed.scheme or "").lower()
    if not (scheme in SUPPORTED_PROXY_SCHEMES and parsed.hostname and port):
        return None
    if not (1 <= port <= 65535):
        return None
    if scheme == "mtproto":
        # The URL scheme users type is "mtproto"; the dict scheme
        # Kurigram expects is "mtproxy" (its ProxyScheme value), and
        # the secret travels under the "secret" key, never as a
        # username/password. Secret comes from the userinfo part
        # (mtproto://<secret>@host:port) or the ?secret= query.
        secret = None
        if parsed.username:
            secret = urllib.parse.unquote(parsed.username)
        if not secret:
            query = urllib.parse.parse_qs(parsed.query)
            values = query.get("secret")
            if values:
                secret = values[0]
        if not secret:
            return None
        try:
            secret = normalize_mtproto_secret(secret)
        except ValueError:
            return None
        return {
            "scheme": "mtproxy",
            "hostname": parsed.hostname,
            "port": port,
            "secret": secret,
        }
    proxy = {
        "scheme": scheme,
        "hostname": parsed.hostname,
        "port": port,
    }
    if parsed.username:
        proxy["username"] = urllib.parse.unquote(parsed.username)
    if parsed.password:
        proxy["password"] = urllib.parse.unquote(parsed.password)
    return proxy


def _format_host_for_url(hostname: str) -> str:
    """若 hostname 为未加括号的 IPv6 地址，包裹为 [ipv6]，以兼容标准 URL 解析。"""
    if ":" in hostname and not (hostname.startswith("[") and hostname.endswith("]")):
        return f"[{hostname}]"
    return hostname


def format_proxy_url(raw: str | dict | None) -> Optional[str]:
    """将代理字符串或字典格式化为标准 URL（如 socks5://127.0.0.1:1080 或 http://user:pass@host:port）。"""
    if not raw:
        return None
    if isinstance(raw, str):
        proxy_dict = build_proxy_dict(raw)
    elif isinstance(raw, dict):
        proxy_dict = raw
    else:
        return None
    if not proxy_dict or not proxy_dict.get("hostname") or not proxy_dict.get("port"):
        return None
    scheme = str(proxy_dict.get("scheme", "http")).lower()
    if scheme == "mtproxy":
        # Display form uses the user-facing scheme and never carries
        # the secret: mtproto://host:port.
        scheme = "mtproto"
    host = _format_host_for_url(str(proxy_dict["hostname"]))
    user = proxy_dict.get("username")
    pwd = proxy_dict.get("password")
    if user:
        quoted_user = urllib.parse.quote(str(user), safe="")
        if pwd:
            quoted_pwd = urllib.parse.quote(str(pwd), safe="")
            auth = f"{quoted_user}:{quoted_pwd}@"
        else:
            auth = f"{quoted_user}@"
    else:
        auth = ""
    return f"{scheme}://{auth}{host}:{proxy_dict['port']}"


def _make_proxy_cache_key(proxy_dict: dict) -> str:
    scheme = str(proxy_dict.get("scheme", "")).lower()
    host = str(proxy_dict.get("hostname", "")).lower()
    port = str(proxy_dict.get("port", ""))
    user = str(proxy_dict.get("username") or "")
    pwd = str(proxy_dict.get("password") or "")
    secret = str(proxy_dict.get("secret") or "")
    if secret:
        # Same host:port with a different secret is a different proxy;
        # only the hash goes into the key, never the secret itself.
        return f"{scheme}://{user}:{pwd}:{secret_cache_hash(secret)}@{host}:{port}"
    return f"{scheme}://{user}:{pwd}@{host}:{port}"


async def _fetch_ip_direct(endpoint: str, timeout: float = 5.0) -> Optional[str]:
    """直连请求 IP 探测端点（禁止 trust_env）。"""
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
            resp = await client.get(
                endpoint, headers={"User-Agent": "TG-SignPulse-Probe/1.0"}
            )
            resp.raise_for_status()
            text = resp.text.strip()
            ipaddress.ip_address(text)
            return text
    except Exception as exc:
        logger.debug("Direct IP probe endpoint %s failed: %s", endpoint, exc)
        return None


async def get_host_direct_ip(timeout: float = 5.0) -> Optional[str]:
    """探测并缓存服务器宿主机的直连出口 IP（TTL=600s）。"""
    cached = _HOST_IP_CACHE.get("host_exit_ip")
    if cached is not None:
        return cached

    for endpoint in DEFAULT_PROBE_ENDPOINTS:
        ip = await _fetch_ip_direct(endpoint, timeout=timeout)
        if ip:
            _HOST_IP_CACHE.set("host_exit_ip", ip)
            return ip
    return None


def _fetch_socks_sync(proxy_dict: dict, endpoint: str, timeout: float) -> str:
    """使用 PySocks 同步请求 socks 代理（在线程池运行）。"""
    if SocksiPyHandler is None or socks is None:
        raise RuntimeError("PySocks is not installed")
    scheme = str(proxy_dict.get("scheme", "socks5")).lower()
    proxy_type = socks.SOCKS4 if scheme == "socks4" else socks.SOCKS5
    handler = SocksiPyHandler(
        proxy_type,
        str(proxy_dict["hostname"]),
        int(proxy_dict["port"]),
        username=proxy_dict.get("username"),
        password=proxy_dict.get("password"),
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), handler)
    req = urllib.request.Request(
        endpoint, headers={"User-Agent": "TG-SignPulse-Probe/1.0"}
    )
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8").strip()


def _summarize_probe_error(exc: BaseException) -> str:
    """Short, log-safe summary of a probe failure (no secrets, no bodies)."""
    name = type(exc).__name__
    text = str(exc).strip().replace("\n", " ")[:160]
    if text:
        return f"{name}: {text}"
    return name


async def _fetch_ip_via_proxy_detailed(
    proxy_dict: dict, endpoint: str, timeout: float = 8.0
) -> tuple[Optional[str], Optional[str]]:
    """Through-proxy IP fetch returning ``(ip, error_summary)``.

    The error summary is the last hop's real failure (ProxyError/407/
    CONNECT refused/TLS ...) instead of a constant "Connection timed
    out", so a failed probe can say why it failed.
    """
    scheme = str(proxy_dict.get("scheme", "http")).lower()
    if scheme in ("http", "https"):
        user = proxy_dict.get("username")
        pwd = proxy_dict.get("password")
        if user:
            quoted_user = urllib.parse.quote(str(user), safe="")
            if pwd:
                quoted_pwd = urllib.parse.quote(str(pwd), safe="")
                auth = f"{quoted_user}:{quoted_pwd}@"
            else:
                auth = f"{quoted_user}@"
        else:
            auth = ""
        host = _format_host_for_url(str(proxy_dict["hostname"]))
        proxy_url = f"{scheme}://{auth}{host}:{proxy_dict['port']}"
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url, trust_env=False, timeout=timeout
            ) as client:
                resp = await client.get(
                    endpoint, headers={"User-Agent": "TG-SignPulse-Probe/1.0"}
                )
                resp.raise_for_status()
                text = resp.text.strip()
                ipaddress.ip_address(text)
                return text, None
        except Exception as exc:
            logger.debug("HTTP proxy probe failed via %s: %s", endpoint, exc)
            return None, _summarize_probe_error(exc)
    elif scheme in ("socks4", "socks5"):
        try:
            text = await asyncio.to_thread(
                _fetch_socks_sync, proxy_dict, endpoint, timeout
            )
            ipaddress.ip_address(text)
            return text, None
        except Exception as exc:
            logger.debug("SOCKS proxy probe failed via %s: %s", endpoint, exc)
            return None, _summarize_probe_error(exc)
    return None, f"Unsupported proxy scheme: {scheme}"


async def _fetch_ip_via_proxy(
    proxy_dict: dict, endpoint: str, timeout: float = 8.0
) -> Optional[str]:
    """Through-proxy IP fetch (ip only; see _fetch_ip_via_proxy_detailed)."""
    ip, _err = await _fetch_ip_via_proxy_detailed(proxy_dict, endpoint, timeout)
    return ip


async def _fetch_exit_ip(
    proxy_dict: dict, timeout: float = PROBE_ENDPOINT_TIMEOUT_SECONDS
) -> tuple[Optional[str], Optional[str]]:
    """Fetch the proxy exit IP, first success wins.

    Endpoints run in parallel (one timeout budget, not one per endpoint).
    For scheme "http", if both https endpoints fail, retry the plaintext
    http variants -- an http-only proxy that cannot CONNECT to https
    would otherwise always report UNAVAILABLE. Returns
    ``(ip, last_error_summary)``.
    """
    endpoints = list(DEFAULT_PROBE_ENDPOINTS)
    results = await asyncio.gather(
        *(
            _fetch_ip_via_proxy_detailed(proxy_dict, endpoint, timeout)
            for endpoint in endpoints
        )
    )
    last_err: Optional[str] = None
    for ip, err in results:
        if ip:
            return ip, None
        if err:
            last_err = err
    if str(proxy_dict.get("scheme", "")).lower() == "http":
        plaintext_results = await asyncio.gather(
            *(
                _fetch_ip_via_proxy_detailed(proxy_dict, endpoint, timeout)
                for endpoint in PROBE_HTTP_PLAINTEXT_ENDPOINTS
            )
        )
        for ip, err in plaintext_results:
            if ip:
                return ip, None
            if err:
                last_err = err
    return None, last_err


async def probe_proxy_exit(
    proxy_dict: dict | None,
    timeout: float = 8.0,
    host_ip: Optional[str] = None,
    *,
    force: bool = False,
    manual: bool = False,
) -> ProxyProbeOutcome:
    """
    探测代理出口 IP 并与本地基线对比。
    - 若出口 IP 等于宿主机直连出口 IP，返回 PROXY_PROBE_FAILED
    - 若端点不可达或超时，返回 PROXY_PROBE_UNAVAILABLE
    - 否则返回 OK 及探测到的出口 IP
    具备 TTL=600s 缓存；UNAVAILABLE 同样入缓存，避免代理不可达时
    每次账号操作都阻塞在重复探测上。
    MTProxy 例外：MTProto 不是 HTTP，出口 IP 探针无法穿过它，
    因此改走真实的 MTProxy 握手探测（见 probe_mtproxy_handshake）：
    握手成功返回 OK，失败返回 UNAVAILABLE。TCP 端口通但握手失败
    时绝不算通过。

    ``force=True`` 跳过缓存读，强制走真实网络探测（手动测试按钮用）。
    ``manual=True`` 表示用户主动点测：绕过熔断门禁，失败结果不写入
    缓存、不累计熔断计数 -- 熔断只由门禁/批量探测驱动。

    探测顺序：缓存 → 熔断门禁 → 5s Telegram 原生握手快筛
    （HTTP CONNECT / SOCKS5 / SOCKS4，见 proxy_handshake.py）→
    出口 IP 校验（https 端点并行；http 代理失败时再试明文端点）。
    握手快筛失败直接返回细粒度错误码，不再烧掉端点超时。

    返回 ProxyProbeOutcome：可按 ``(status, detail)`` 解包，另带
    实测 ``latency_ms``（缓存命中时为 None，绝不把 0ms 当实测延迟）
    与 ``cached`` / ``code``。
    """
    if not proxy_dict:
        return ProxyProbeOutcome(
            status=ProxyProbeStatus.UNAVAILABLE,
            detail="No proxy configured",
            code="PROXY_NOT_CONFIGURED",
        )

    cache_key = _make_proxy_cache_key(proxy_dict)
    if not force:
        cached = _PROBE_CACHE.get(cache_key)
        if cached is not None:
            status, detail = cached
            return ProxyProbeOutcome(
                status=status,
                detail=detail,
                latency_ms=None,
                cached=True,
                code=(
                    "OK"
                    if status == ProxyProbeStatus.OK
                    else (
                        "PROXY_PROBE_FAILED"
                        if status == ProxyProbeStatus.FAILED
                        else "PROXY_PROBE_UNAVAILABLE"
                    )
                ),
            )

    from backend.services.proxy_circuit_breaker import get_proxy_circuit_breaker

    breaker = get_proxy_circuit_breaker()
    if not manual and not await breaker.is_available(proxy_dict):
        return ProxyProbeOutcome(
            status=ProxyProbeStatus.UNAVAILABLE,
            detail="Proxy circuit breaker tripped (cooling down or unreachable)",
            code="PROXY_CIRCUIT_BREAKER",
        )

    probe_started = time.monotonic()

    def _latency_ms() -> int:
        return max(int((time.monotonic() - probe_started) * 1000), 0)

    def _record(result: ProxyProbeOutcome) -> ProxyProbeOutcome:
        # 熔断计数只由门禁/批量探测驱动：手动测试不 record 成功/失败。
        if not manual:
            if result.status == ProxyProbeStatus.OK:
                breaker.record_success(proxy_dict)
            else:
                breaker.record_failure(proxy_dict)
        # 手动测试的失败结果不写入缓存（下次点测仍真实探测）；
        # 手动 OK 可以入缓存，供随后的门禁复用。
        if not manual or result.status == ProxyProbeStatus.OK:
            _PROBE_CACHE.set(cache_key, (result.status, result.detail))
        return result

    if str(proxy_dict.get("scheme", "")).lower() == "mtproxy":
        # MTProto carries no HTTP: the only meaningful check is the
        # real handshake against a DC (see backend/utils/mtproxy.py).
        # A bare TCP port-open must never count as "proxy works".
        # Latency is the TCP connect time only; the 2.0s confirmation
        # wait is liveness validation, not latency.
        ok, detail, connect_ms = await probe_mtproxy_handshake(
            proxy_dict, timeout=timeout
        )
        if ok:
            return _record(
                ProxyProbeOutcome(
                    status=ProxyProbeStatus.OK,
                    detail=detail,
                    latency_ms=connect_ms,
                    code="OK",
                )
            )
        return _record(
            ProxyProbeOutcome(
                status=ProxyProbeStatus.UNAVAILABLE,
                detail=detail,
                latency_ms=None,
                code="PROXY_HANDSHAKE_FAILED",
            )
        )

    # Fast screen: real protocol handshake to Telegram itself (5s).
    # A proxy that cannot CONNECT to Telegram cannot serve accounts,
    # and the fine-grained code says why -- no need to burn the
    # endpoint timeout budget afterwards.
    handshake = await probe_telegram_handshake(
        proxy_dict,
        timeout=min(PROBE_HANDSHAKE_TIMEOUT_SECONDS, timeout),
    )
    if not handshake.ok:
        return _record(
            ProxyProbeOutcome(
                status=ProxyProbeStatus.UNAVAILABLE,
                detail=f"{handshake.code}: {handshake.message}",
                latency_ms=handshake.latency_ms,
                code=handshake.code,
            )
        )

    if host_ip is None:
        host_ip = await get_host_direct_ip(timeout=min(timeout, 5.0))

    detected_ip, last_err = await _fetch_exit_ip(
        proxy_dict, timeout=min(timeout, PROBE_ENDPOINT_TIMEOUT_SECONDS)
    )

    if detected_ip is not None:
        if host_ip is None:
            # Baseline direct IP probe failed: cannot prove proxy is not leaking host direct IP.
            # Fail closed to prevent silent leaks.
            return _record(
                ProxyProbeOutcome(
                    status=ProxyProbeStatus.UNAVAILABLE,
                    detail="Host direct IP baseline probe unavailable",
                    latency_ms=_latency_ms(),
                    code="PROXY_PROBE_UNAVAILABLE",
                )
            )
        elif detected_ip == host_ip:
            return _record(
                ProxyProbeOutcome(
                    status=ProxyProbeStatus.FAILED,
                    detail=detected_ip,
                    latency_ms=_latency_ms(),
                    code="PROXY_PROBE_FAILED",
                )
            )
        else:
            return _record(
                ProxyProbeOutcome(
                    status=ProxyProbeStatus.OK,
                    detail=detected_ip,
                    latency_ms=_latency_ms(),
                    code="OK",
                )
            )
    return _record(
        ProxyProbeOutcome(
            status=ProxyProbeStatus.UNAVAILABLE,
            detail=last_err or "Connection timed out",
            latency_ms=_latency_ms(),
            code="PROXY_PROBE_UNAVAILABLE",
        )
    )
