"""MTProxy support tests (fork patch).

Covers secret validation, tg:// share-link parsing, proxy-dict
building, secret hiding in display URLs / cache keys, Kurigram
transport dispatch (mocked, no network), the handshake probe
against a closed local port (fail closed, no network), and the
passkey route accepting mtproto.

End-to-end login through a real MTProxy server is NOT covered
here: it needs a live MTProxy server + its secret, and must be
tested on the deployed panel by the owner.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.utils.mtproxy import (
    normalize_mtproto_secret,
    parse_mtproxy_link,
    probe_mtproxy_handshake,
)
from backend.utils.proxy import (
    _make_proxy_cache_key,
    build_proxy_dict,
    format_proxy_url,
    normalize_proxy_url,
)

PLAIN_HEX = "0123456789abcdef0123456789abcdef"
DD_HEX = "dd" + PLAIN_HEX
# ee secret: marker + 16-byte key + ASCII SNI domain, all hex.
EE_HEX = "ee" + PLAIN_HEX + "example.com".encode().hex()
B64_SECRET = "AAECAwQFBgcICQoLDA0ODw=="  # 16 bytes 00..0f, base64


def test_normalize_plain_hex():
    assert normalize_mtproto_secret(PLAIN_HEX) == PLAIN_HEX


def test_normalize_dd_hex():
    assert normalize_mtproto_secret(DD_HEX) == DD_HEX


def test_normalize_ee_hex_with_domain():
    assert normalize_mtproto_secret(EE_HEX) == EE_HEX


def test_normalize_base64():
    assert normalize_mtproto_secret(B64_SECRET) == B64_SECRET


def test_normalize_rejects_short_secret():
    with pytest.raises(ValueError):
        normalize_mtproto_secret("0123abcd")


def test_normalize_rejects_bad_characters():
    with pytest.raises(ValueError):
        normalize_mtproto_secret("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")


def test_normalize_rejects_17_bytes_without_dd_marker():
    # 17 bytes whose first byte is not 0xdd.
    with pytest.raises(ValueError):
        normalize_mtproto_secret("aa" + PLAIN_HEX)


def test_normalize_rejects_ee_without_domain():
    # ee marker + key but no domain bytes after it.
    with pytest.raises(ValueError):
        normalize_mtproto_secret("ee" + PLAIN_HEX)


def test_parse_tg_proxy_link():
    url = parse_mtproxy_link(f"tg://proxy?server=1.2.3.4&port=443&secret={PLAIN_HEX}")
    assert url == f"mtproto://{PLAIN_HEX}@1.2.3.4:443"


def test_parse_tme_proxy_link():
    url = parse_mtproxy_link(
        f"https://t.me/proxy?server=1.2.3.4&port=443&secret={PLAIN_HEX}"
    )
    assert url == f"mtproto://{PLAIN_HEX}@1.2.3.4:443"


def test_parse_link_rejects_missing_secret():
    assert parse_mtproxy_link("tg://proxy?server=1.2.3.4&port=443") is None


def test_parse_link_rejects_non_link():
    assert parse_mtproxy_link("socks5://1.2.3.4:1080") is None
    assert parse_mtproxy_link("not a proxy") is None


def test_build_proxy_dict_mtproto_url():
    d = build_proxy_dict(f"mtproto://{PLAIN_HEX}@1.2.3.4:443")
    assert d == {
        "scheme": "mtproxy",
        "hostname": "1.2.3.4",
        "port": 443,
        "secret": PLAIN_HEX,
    }


def test_build_proxy_dict_mtproto_link():
    d = build_proxy_dict(f"tg://proxy?server=1.2.3.4&port=443&secret={PLAIN_HEX}")
    assert d is not None
    assert d["scheme"] == "mtproxy"
    assert d["hostname"] == "1.2.3.4"
    assert d["secret"] == PLAIN_HEX


def test_build_proxy_dict_mtproto_requires_secret():
    assert build_proxy_dict("mtproto://1.2.3.4:443") is None
    assert build_proxy_dict(f"mtproto://{PLAIN_HEX[:8]}@1.2.3.4:443") is None


def test_build_proxy_dict_socks5_unchanged():
    d = build_proxy_dict("socks5://user:pass@1.2.3.4:1080")
    assert d == {
        "scheme": "socks5",
        "hostname": "1.2.3.4",
        "port": 1080,
        "username": "user",
        "password": "pass",
    }


def test_normalize_proxy_url_keeps_mtproto():
    assert (
        normalize_proxy_url(f"mtproto://{PLAIN_HEX}@1.2.3.4:443")
        == f"mtproto://{PLAIN_HEX}@1.2.3.4:443"
    )


def test_format_proxy_url_hides_secret():
    d = build_proxy_dict(f"mtproto://{PLAIN_HEX}@1.2.3.4:443")
    url = format_proxy_url(d)
    assert url == "mtproto://1.2.3.4:443"
    assert PLAIN_HEX not in (url or "")


def test_cache_key_differs_by_secret_and_hides_it():
    d1 = build_proxy_dict(f"mtproto://{PLAIN_HEX}@1.2.3.4:443")
    d2 = build_proxy_dict(f"mtproto://{DD_HEX}@1.2.3.4:443")
    k1, k2 = _make_proxy_cache_key(d1), _make_proxy_cache_key(d2)
    assert k1 != k2
    assert PLAIN_HEX not in k1
    assert DD_HEX not in k2


@pytest.mark.asyncio
async def test_kurigram_dispatch_mtproto():
    """Kurigram routes an mtproxy dict to _connect_via_mtproxy."""
    from pyrogram.connection.proxy import normalize_proxy
    from pyrogram.connection.transport import TCPAbridged

    proxy = normalize_proxy(
        {"scheme": "mtproxy", "hostname": "1.2.3.4", "port": 443, "secret": PLAIN_HEX}
    )
    transport = TCPAbridged(proxy=proxy, dc_id=1)
    transport._connect_via_mtproxy = AsyncMock()
    transport._connect_via_proxy = AsyncMock()
    await transport._connect(("149.154.175.53", 443))
    transport._connect_via_mtproxy.assert_awaited_once()
    transport._connect_via_proxy.assert_not_awaited()
    await transport.close()


@pytest.mark.asyncio
async def test_kurigram_dispatch_socks5_unchanged():
    from pyrogram.connection.proxy import normalize_proxy
    from pyrogram.connection.transport import TCPAbridged

    proxy = normalize_proxy({"scheme": "socks5", "hostname": "1.2.3.4", "port": 1080})
    transport = TCPAbridged(proxy=proxy, dc_id=1)
    transport._connect_via_mtproxy = AsyncMock()
    transport._connect_via_proxy = AsyncMock()
    await transport._connect(("149.154.175.53", 443))
    transport._connect_via_proxy.assert_awaited_once()
    transport._connect_via_mtproxy.assert_not_awaited()
    await transport.close()


def test_transport_class_for_secret_flavours():
    from pyrogram.connection.connection import transport_class_for
    from pyrogram.connection.proxy import normalize_proxy
    from pyrogram.connection.transport import (
        TCPAbridged,
        TCPIntermediatePadded,
    )

    plain = normalize_proxy(
        {"scheme": "mtproxy", "hostname": "1.2.3.4", "port": 443, "secret": PLAIN_HEX}
    )
    assert transport_class_for(plain) is TCPAbridged
    padded = normalize_proxy(
        {"scheme": "mtproxy", "hostname": "1.2.3.4", "port": 443, "secret": DD_HEX}
    )
    assert transport_class_for(padded) is TCPIntermediatePadded


@pytest.mark.asyncio
async def test_probe_handshake_closed_port_fails_closed():
    """No proxy on 127.0.0.1:1: the probe must fail, never pass."""
    ok, detail, latency_ms = await probe_mtproxy_handshake(
        {"scheme": "mtproxy", "hostname": "127.0.0.1", "port": 1, "secret": PLAIN_HEX},
        timeout=5.0,
    )
    assert ok is False
    assert detail
    assert PLAIN_HEX not in detail
    assert latency_ms is None


@pytest.mark.asyncio
async def test_probe_handshake_latency_excludes_confirmation_wait(monkeypatch):
    """握手成功时 latency 约等于连接耗时，不含 2.0s recv 确认等待。"""
    import asyncio

    class FakeTransport:
        def __init__(self, *args, **kwargs):
            pass

        async def connect(self, address):
            await asyncio.sleep(0.05)

        async def recv(self, n):
            raise asyncio.TimeoutError()

        async def close(self):
            pass

    monkeypatch.setattr(
        "pyrogram.connection.connection.transport_class_for",
        lambda proxy: FakeTransport,
    )
    monkeypatch.setattr(
        "pyrogram.connection.proxy.normalize_proxy",
        lambda proxy_dict: SimpleNamespace(hostname="1.2.3.4", port=443),
    )

    ok, detail, latency_ms = await probe_mtproxy_handshake(
        {"scheme": "mtproxy", "hostname": "1.2.3.4", "port": 443, "secret": PLAIN_HEX},
        timeout=5.0,
    )
    assert ok is True
    assert detail == "MTProxy handshake OK"
    # 连接耗时约 50ms；若把 2.0s 确认等待计入，延迟会是 ~2050ms
    assert latency_ms is not None
    assert 30 <= latency_ms < 1000


def test_telethon_proxy_kwargs_mtproto():
    from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate

    from backend.api.routes.passkey import _telethon_proxy_kwargs

    kwargs = _telethon_proxy_kwargs(f"mtproto://{PLAIN_HEX}@1.2.3.4:443")
    assert kwargs["connection"] is ConnectionTcpMTProxyRandomizedIntermediate
    assert kwargs["proxy"] == ("1.2.3.4", 443, PLAIN_HEX)


def test_telethon_proxy_kwargs_socks5_unchanged():
    from backend.api.routes.passkey import _telethon_proxy_kwargs

    kwargs = _telethon_proxy_kwargs("socks5://user:pass@1.2.3.4:1080")
    assert kwargs == {
        "proxy": {
            "proxy_type": "socks5",
            "addr": "1.2.3.4",
            "port": 1080,
            "rdns": True,
            "username": "user",
            "password": "pass",
        }
    }


def test_telethon_proxy_kwargs_rejects_bad_proxy():
    from fastapi import HTTPException

    from backend.api.routes.passkey import _telethon_proxy_kwargs

    with pytest.raises(HTTPException):
        _telethon_proxy_kwargs("mtproto://1.2.3.4:443")
