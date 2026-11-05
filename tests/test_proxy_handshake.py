"""Tests for the native Telegram-target handshake probe (proxy_handshake).

Fake local proxy servers (HTTP CONNECT / SOCKS5 / SOCKS4) answer on
127.0.0.1 ephemeral ports; no real network is used.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.utils.proxy_handshake import (
    PROBE_HANDSHAKE_TIMEOUT_SECONDS,
    probe_telegram_handshake,
)


async def _serve(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


@pytest.mark.asyncio
async def test_http_connect_200_ok():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {"scheme": "http", "hostname": "127.0.0.1", "port": port},
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is True
    assert result.code == "OK"
    assert result.latency_ms is not None and result.latency_ms >= 0


@pytest.mark.asyncio
async def test_http_connect_407_auth_failed():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {"scheme": "http", "hostname": "127.0.0.1", "port": port},
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is False
    assert result.code == "PROXY_AUTH_FAILED"
    assert "认证" in result.message


@pytest.mark.asyncio
async def test_http_connect_tunnel_rejected():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {"scheme": "http", "hostname": "127.0.0.1", "port": port},
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is False
    assert result.code == "PROXY_TUNNEL_REJECTED"


@pytest.mark.asyncio
async def test_socks5_no_auth_ok():
    async def handler(reader, writer):
        greeting = await reader.readexactly(3)
        assert greeting[0] == 0x05
        writer.write(b"\x05\x00")
        await writer.drain()
        req = await reader.readexactly(4)
        assert req[0] == 0x05 and req[3] == 0x03  # domain form
        addr_len = req[3] and (await reader.readexactly(1))[0]
        await reader.readexactly(addr_len + 2)
        writer.write(b"\x05\x00\x00\x01\x01\x02\x03\x04\x00\x50")
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {"scheme": "socks5", "hostname": "127.0.0.1", "port": port},
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is True
    assert result.code == "OK"


@pytest.mark.asyncio
async def test_socks5_user_pass_ok():
    async def handler(reader, writer):
        greeting = await reader.readexactly(4)
        assert greeting[0] == 0x05 and greeting[1] == 0x02
        writer.write(b"\x05\x02")
        await writer.drain()
        sub = await reader.readexactly(2)
        assert sub[0] == 0x01
        await reader.readexactly(sub[1])
        plen = (await reader.readexactly(1))[0]
        await reader.readexactly(plen)
        writer.write(b"\x01\x00")
        await writer.drain()
        await reader.readexactly(4)
        addr_len = (await reader.readexactly(1))[0]
        await reader.readexactly(addr_len + 2)
        writer.write(b"\x05\x00\x00\x01\x01\x02\x03\x04\x00\x50")
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {
                "scheme": "socks5",
                "hostname": "127.0.0.1",
                "port": port,
                "username": "alice",
                "password": "secret",
            },
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is True


@pytest.mark.asyncio
async def test_socks5_rejected_tunnel():
    async def handler(reader, writer):
        await reader.readexactly(3)
        writer.write(b"\x05\x00")
        await writer.drain()
        await reader.readexactly(4)
        addr_len = (await reader.readexactly(1))[0]
        await reader.readexactly(addr_len + 2)
        writer.write(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {"scheme": "socks5", "hostname": "127.0.0.1", "port": port},
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is False
    assert result.code == "PROXY_TUNNEL_REJECTED"


@pytest.mark.asyncio
async def test_socks4_ok():
    async def handler(reader, writer):
        req = await reader.readexactly(8)
        assert req[0] == 0x04 and req[1] == 0x01
        await reader.readuntil(b"\x00")
        writer.write(b"\x00\x5a\x00\x50\x01\x02\x03\x04")
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {"scheme": "socks4", "hostname": "127.0.0.1", "port": port},
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is True


@pytest.mark.asyncio
async def test_closed_port_refused():
    result = await probe_telegram_handshake(
        {"scheme": "http", "hostname": "127.0.0.1", "port": 1},
        timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
    )
    assert result.ok is False
    assert result.code == "PROXY_CONNECT_REFUSED"


@pytest.mark.asyncio
async def test_unsupported_scheme():
    result = await probe_telegram_handshake(
        {"scheme": "mtproxy", "hostname": "127.0.0.1", "port": 443},
        timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
    )
    assert result.ok is False
    assert result.code == "PROXY_PROTOCOL_UNSUPPORTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 405, 501])
async def test_http_connect_unsupported_tcp_ok_fail_closed(status):
    """HTTP-only proxies (no CONNECT support) answer 400/405/501.

    The verdict stays failed (fail closed: such a proxy cannot carry
    Telegram), but the code says WHY and tcp_ok=True says the port is
    reachable -- the panel shows both dimensions instead of downgrading
    the verdict to "reachable" like telegram-manager's TCP-only test.
    """

    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(f"HTTP/1.1 {status} Bad Request\r\n\r\n".encode("ascii"))
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {"scheme": "http", "hostname": "127.0.0.1", "port": port},
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is False
    assert result.code == "PROXY_CONNECT_UNSUPPORTED"
    assert result.tcp_ok is True


@pytest.mark.asyncio
async def test_http_connect_502_stays_tunnel_rejected_with_tcp_ok():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {"scheme": "http", "hostname": "127.0.0.1", "port": port},
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is False
    assert result.code == "PROXY_TUNNEL_REJECTED"
    assert result.tcp_ok is True


@pytest.mark.asyncio
async def test_http_connect_ok_reports_tcp_ok():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        writer.close()

    server, port = await _serve(handler)
    try:
        result = await probe_telegram_handshake(
            {"scheme": "http", "hostname": "127.0.0.1", "port": port},
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
    finally:
        server.close()
    assert result.ok is True
    assert result.tcp_ok is True


@pytest.mark.asyncio
async def test_tcp_unreachable_reports_tcp_not_ok():
    result = await probe_telegram_handshake(
        {"scheme": "http", "hostname": "127.0.0.1", "port": 1},
        timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
    )
    assert result.ok is False
    assert result.tcp_ok is False
