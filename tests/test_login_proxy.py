"""Tests for login-time proxy selection (Round 4 item 1).

resolve_login_proxy is pure except for the pool lookup (DB) and the
config service; both are stubbed here. Every branch must fail closed:
a bad choice raises, it never silently falls back to direct.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.telegram.login_proxy import resolve_login_proxy


class FakeCfg:
    def __init__(self, *, global_proxy=None, require_proxy=False):
        self._global_proxy = global_proxy
        self._require_proxy = require_proxy

    def get_global_proxy(self):
        return self._global_proxy

    def require_proxy_for_telegram(self):
        return self._require_proxy


def _fake_row(monkeypatch):
    row = SimpleNamespace(
        ptype="http", host="127.0.0.1", port=8080, username=None, password=None
    )
    monkeypatch.setattr(
        "backend.services.telegram.proxy_store.get_proxy_row",
        lambda proxy_id: row if proxy_id == "pool1" else None,
    )
    return row


def test_pool_ok_returns_pool_dict(monkeypatch):
    _fake_row(monkeypatch)
    sel = resolve_login_proxy(
        proxy_mode="pool", proxy_id="pool1", config_service=FakeCfg()
    )
    assert sel.mode == "pool"
    assert sel.proxy_id == "pool1"
    assert sel.proxy_dict is not None
    assert sel.proxy_dict["hostname"] == "127.0.0.1"
    assert sel.persist_kwargs() == {
        "proxy_mode": "pool",
        "proxy_id": "pool1",
        "proxy": "",
    }


def test_pool_unknown_id_raises_not_found(monkeypatch):
    _fake_row(monkeypatch)
    with pytest.raises(ValueError, match="PROXY_NOT_FOUND"):
        resolve_login_proxy(
            proxy_mode="pool", proxy_id="nope", config_service=FakeCfg()
        )


def test_pool_without_id_raises_not_found(monkeypatch):
    _fake_row(monkeypatch)
    with pytest.raises(ValueError, match="PROXY_NOT_FOUND"):
        resolve_login_proxy(proxy_mode="pool", config_service=FakeCfg())


def test_custom_ok_and_invalid():
    sel = resolve_login_proxy(
        proxy_mode="custom",
        proxy="socks5://127.0.0.1:1080",
        config_service=FakeCfg(),
    )
    assert sel.mode == "custom"
    assert sel.proxy_dict["scheme"] == "socks5"
    assert sel.persist_kwargs()["proxy"] == "socks5://127.0.0.1:1080"
    with pytest.raises(ValueError, match="PROXY_INVALID_BLOCKED"):
        resolve_login_proxy(
            proxy_mode="custom", proxy="not a proxy url", config_service=FakeCfg()
        )
    with pytest.raises(ValueError, match="PROXY_INVALID_BLOCKED"):
        resolve_login_proxy(proxy_mode="custom", config_service=FakeCfg())


def test_direct_ok_and_blocked_when_proxy_required():
    sel = resolve_login_proxy(proxy_mode="direct", config_service=FakeCfg())
    assert sel.mode == "direct"
    assert sel.proxy_dict is None
    assert sel.proxy_url is None
    with pytest.raises(ValueError, match="PROXY_REQUIRED_BLOCKED"):
        resolve_login_proxy(
            proxy_mode="direct", config_service=FakeCfg(require_proxy=True)
        )


def test_inherit_falls_back_to_global_proxy():
    sel = resolve_login_proxy(
        config_service=FakeCfg(global_proxy="http://127.0.0.1:8080")
    )
    assert sel.mode == "inherit"
    assert sel.proxy_url == "http://127.0.0.1:8080"
    assert sel.proxy_dict is not None


def test_inherit_without_global_is_direct_unless_required():
    sel = resolve_login_proxy(config_service=FakeCfg())
    assert sel.mode == "inherit"
    assert sel.proxy_dict is None
    with pytest.raises(ValueError, match="PROXY_REQUIRED_BLOCKED"):
        resolve_login_proxy(config_service=FakeCfg(require_proxy=True))


def test_mode_inferred_from_proxy_id_and_url(monkeypatch):
    _fake_row(monkeypatch)
    sel = resolve_login_proxy(proxy_id="pool1", config_service=FakeCfg())
    assert sel.mode == "pool"
    sel = resolve_login_proxy(proxy="http://127.0.0.1:8080", config_service=FakeCfg())
    assert sel.mode == "custom"
    with pytest.raises(ValueError, match="PROXY_INVALID_BLOCKED"):
        resolve_login_proxy(proxy_mode="bogus", config_service=FakeCfg())
