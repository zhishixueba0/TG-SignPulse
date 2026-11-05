"""Native Telegram-target proxy handshake probe (Drive 1.0.129 logic, ported).

Probes ``api.telegram.org:443`` through the proxy with a real protocol
handshake (HTTP CONNECT / SOCKS5 / SOCKS4) using only ``asyncio`` sockets --
no PySocks thread pool, no httpx, no third-party IP service. Single attempt
timeout is 5.0s and the returned latency is the measured wall time.

This is a fast pre-screen only: it proves the proxy can reach Telegram, but
it cannot prove traffic actually exits via the proxy -- the exit-IP check in
``backend.utils.proxy.probe_proxy_exit`` still runs afterwards. MTProto is
NOT handled here (see ``backend.utils.mtproxy.probe_mtproxy_handshake``);
1.0.129 explicitly did not support MTProto either.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import socket
import struct
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("backend.utils.proxy_handshake")

# Fixed probe target: Telegram itself, not a third-party IP service. If the
# IP services are walled off the proxy may still be fine; if Telegram is
# unreachable through it, the proxy is useless for this panel either way.
_PROBE_TARGET_HOST = "api.telegram.org"
_PROBE_TARGET_PORT = 443
PROBE_HANDSHAKE_TIMEOUT_SECONDS = 5.0

# Fine-grained probe error codes (Drive 1.0.129 `_TEST_MESSAGES` set).
PROXY_TEST_MESSAGES: dict[str, str] = {
    "PROXY_DNS_FAILED": "代理域名解析失败：无法解析代理主机",
    "PROXY_CONNECT_REFUSED": "代理拒绝连接：目标端口未监听或被防火墙拦截",
    "PROXY_CONNECT_TIMEOUT": "连接超时：5 秒内代理没有响应",
    "PROXY_CONNECT_FAILED": "代理连接失败",
    "PROXY_AUTH_REQUIRED": "代理要求认证：请填写用户名和密码",
    "PROXY_AUTH_UNSUPPORTED": "代理认证方式不受支持",
    "PROXY_AUTH_FAILED": "代理认证失败：用户名或密码错误",
    "PROXY_TUNNEL_REJECTED": "代理拒绝转发到 Telegram（出口受限或线路已失效）",
    "PROXY_HANDSHAKE_FAILED": "代理回应异常（协议可能选错：HTTP 与 SOCKS 混用）",
    "PROXY_PROTOCOL_UNSUPPORTED": "不支持的代理协议",
}


@dataclass
class HandshakeResult:
    ok: bool
    code: str
    message: str
    latency_ms: Optional[int] = None


def _result(
    ok: bool, code: str, started: float, *, latency_ms: Optional[int] = None
) -> HandshakeResult:
    if latency_ms is None:
        latency_ms = max(int((time.monotonic() - started) * 1000), 0)
    return HandshakeResult(
        ok=ok,
        code=code,
        message=PROXY_TEST_MESSAGES.get(code, code),
        latency_ms=latency_ms,
    )


def _classify_connect_error(exc: BaseException) -> str:
    """Map a transport-level failure to a probe error code."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "PROXY_CONNECT_TIMEOUT"
    if isinstance(exc, ConnectionRefusedError):
        return "PROXY_CONNECT_REFUSED"
    if isinstance(exc, socket.gaierror):
        return "PROXY_DNS_FAILED"
    if isinstance(exc, OSError):
        return "PROXY_CONNECT_FAILED"
    return "PROXY_HANDSHAKE_FAILED"


async def _read_exactly(reader: asyncio.StreamReader, n: int) -> bytes:
    data = await reader.readexactly(n)
    if len(data) != n:
        raise asyncio.IncompleteReadError(partial=data, expected=n)
    return data


async def _probe_http(
    proxy_dict: dict, timeout: float, started: float
) -> HandshakeResult:
    """HTTP(S) proxy: CONNECT to api.telegram.org:443, expect 200."""
    host = str(proxy_dict["hostname"])
    port = int(proxy_dict["port"])
    user = proxy_dict.get("username")
    password = proxy_dict.get("password")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except Exception as exc:
        return _result(False, _classify_connect_error(exc), started)
    try:
        lines = [
            f"CONNECT {_PROBE_TARGET_HOST}:{_PROBE_TARGET_PORT} HTTP/1.1",
            f"Host: {_PROBE_TARGET_HOST}:{_PROBE_TARGET_PORT}",
        ]
        if user:
            creds = base64.b64encode(f"{user}:{password or ''}".encode("utf-8")).decode(
                "ascii"
            )
            lines.append(f"Proxy-Authorization: Basic {creds}")
        lines.append("")
        lines.append("")
        writer.write("\r\n".join(lines).encode("ascii"))
        await writer.drain()
        try:
            raw_head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=timeout
            )
        except (asyncio.LimitOverrunError, ValueError):
            return _result(False, "PROXY_HANDSHAKE_FAILED", started)
        except Exception as exc:
            return _result(False, _classify_connect_error(exc), started)
        if len(raw_head) > 8192:
            return _result(False, "PROXY_HANDSHAKE_FAILED", started)
        first_line = raw_head.split(b"\r\n", 1)[0].decode("latin-1", "ignore")
        parts = first_line.split()
        if len(parts) < 2 or not parts[1].isdigit():
            return _result(False, "PROXY_HANDSHAKE_FAILED", started)
        status = int(parts[1])
        if status == 200:
            return _result(True, "OK", started)
        if status == 407:
            return _result(False, "PROXY_AUTH_FAILED", started)
        return _result(False, "PROXY_TUNNEL_REJECTED", started)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _probe_socks5(
    proxy_dict: dict, timeout: float, started: float
) -> HandshakeResult:
    """SOCKS5: greeting (+ user/pass sub-negotiation), then CONNECT."""
    host = str(proxy_dict["hostname"])
    port = int(proxy_dict["port"])
    user = proxy_dict.get("username")
    password = proxy_dict.get("password")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except Exception as exc:
        return _result(False, _classify_connect_error(exc), started)
    try:
        try:
            if user:
                writer.write(b"\x05\x02\x00\x02")
            else:
                writer.write(b"\x05\x01\x00")
            await writer.drain()
            ver, method = await asyncio.wait_for(
                _read_exactly(reader, 2), timeout=timeout
            )
            if ver != 0x05:
                return _result(False, "PROXY_HANDSHAKE_FAILED", started)
            if method == 0xFF:
                return _result(False, "PROXY_AUTH_UNSUPPORTED", started)
            if method == 0x02:
                if not user:
                    return _result(False, "PROXY_AUTH_REQUIRED", started)
                user_b = str(user).encode("utf-8")
                pass_b = str(password or "").encode("utf-8")
                if len(user_b) > 255 or len(pass_b) > 255:
                    return _result(False, "PROXY_AUTH_FAILED", started)
                writer.write(
                    b"\x01"
                    + bytes([len(user_b)])
                    + user_b
                    + bytes([len(pass_b)])
                    + pass_b
                )
                await writer.drain()
                auth_ver, auth_status = await asyncio.wait_for(
                    _read_exactly(reader, 2), timeout=timeout
                )
                if auth_ver != 0x01 or auth_status != 0x00:
                    return _result(False, "PROXY_AUTH_FAILED", started)
            elif method != 0x00:
                return _result(False, "PROXY_AUTH_UNSUPPORTED", started)

            target = _PROBE_TARGET_HOST.encode("ascii")
            writer.write(
                b"\x05\x01\x00\x03"
                + bytes([len(target)])
                + target
                + struct.pack(">H", _PROBE_TARGET_PORT)
            )
            await writer.drain()
            resp_ver, rep, _rsv, atyp = await asyncio.wait_for(
                _read_exactly(reader, 4), timeout=timeout
            )
            if resp_ver != 0x05:
                return _result(False, "PROXY_HANDSHAKE_FAILED", started)
            if rep == 0x00:
                # Drain the bound address, then the handshake is proven.
                if atyp == 0x01:
                    await asyncio.wait_for(_read_exactly(reader, 6), timeout=timeout)
                elif atyp == 0x03:
                    (addr_len,) = await asyncio.wait_for(
                        _read_exactly(reader, 1), timeout=timeout
                    )
                    await asyncio.wait_for(
                        _read_exactly(reader, addr_len + 2), timeout=timeout
                    )
                elif atyp == 0x04:
                    await asyncio.wait_for(_read_exactly(reader, 18), timeout=timeout)
                return _result(True, "OK", started)
            if rep in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08):
                return _result(False, "PROXY_TUNNEL_REJECTED", started)
            return _result(False, "PROXY_HANDSHAKE_FAILED", started)
        except (asyncio.TimeoutError, TimeoutError):
            return _result(False, "PROXY_CONNECT_TIMEOUT", started)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            return _result(False, _classify_connect_error(exc), started)
        except Exception:
            return _result(False, "PROXY_HANDSHAKE_FAILED", started)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _probe_socks4(
    proxy_dict: dict, timeout: float, started: float
) -> HandshakeResult:
    """SOCKS4: CONNECT to the resolved IPv4 of api.telegram.org:443."""
    host = str(proxy_dict["hostname"])
    port = int(proxy_dict["port"])
    try:
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(
            loop.getaddrinfo(
                _PROBE_TARGET_HOST,
                _PROBE_TARGET_PORT,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            ),
            timeout=timeout,
        )
    except Exception as exc:
        return _result(False, _classify_connect_error(exc), started)
    if not infos:
        return _result(False, "PROXY_DNS_FAILED", started)
    target_ip = infos[0][4][0]
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except Exception as exc:
        return _result(False, _classify_connect_error(exc), started)
    try:
        try:
            user = str(proxy_dict.get("username") or "")
            request = (
                b"\x04\x01"
                + struct.pack(">H", _PROBE_TARGET_PORT)
                + socket.inet_aton(target_ip)
                + user.encode("utf-8")
                + b"\x00"
            )
            writer.write(request)
            await writer.drain()
            _null, status = await asyncio.wait_for(
                _read_exactly(reader, 2), timeout=timeout
            )
            # SOCKS4 reply is 8 bytes; drain the rest so the peer sees a
            # clean close, but the verdict is already in `status`.
            try:
                await asyncio.wait_for(_read_exactly(reader, 6), timeout=timeout)
            except Exception:
                pass
            if status == 90:
                return _result(True, "OK", started)
            if status == 93:
                return _result(False, "PROXY_AUTH_FAILED", started)
            if status == 91:
                return _result(False, "PROXY_TUNNEL_REJECTED", started)
            return _result(False, "PROXY_TUNNEL_REJECTED", started)
        except (asyncio.TimeoutError, TimeoutError):
            return _result(False, "PROXY_CONNECT_TIMEOUT", started)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            return _result(False, _classify_connect_error(exc), started)
        except Exception:
            return _result(False, "PROXY_HANDSHAKE_FAILED", started)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def probe_telegram_handshake(
    proxy_dict: dict, timeout: float = PROBE_HANDSHAKE_TIMEOUT_SECONDS
) -> HandshakeResult:
    """Real protocol handshake against Telegram through the proxy.

    Returns a HandshakeResult (ok / fine-grained code / Chinese message /
    measured latency). Never raises: transport failures are mapped to codes.
    """
    started = time.monotonic()
    scheme = str(proxy_dict.get("scheme", "")).lower()
    host = proxy_dict.get("hostname")
    port = proxy_dict.get("port")
    if not host or not port:
        return _result(False, "PROXY_CONNECT_FAILED", started)
    try:
        if scheme in ("http", "https"):
            return await _probe_http(proxy_dict, timeout, started)
        if scheme == "socks5":
            return await _probe_socks5(proxy_dict, timeout, started)
        if scheme == "socks4":
            return await _probe_socks4(proxy_dict, timeout, started)
        return _result(False, "PROXY_PROTOCOL_UNSUPPORTED", started)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Telegram handshake probe failed: %s", exc, exc_info=True)
        return _result(False, "PROXY_HANDSHAKE_FAILED", started)
