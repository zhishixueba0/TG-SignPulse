"""Tests for Telegram-native session retention settings (SetAuthorizationTTL /
SetAccountTTL), routes + service mixin."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.telegram.devices import TelegramDevicesMixin
from tests.test_api import _auth, _login, api_client, db  # noqa: F401


class DummyDevicesService(TelegramDevicesMixin):
    def __init__(self):
        self._client = MagicMock()
        self._client.__aenter__ = AsyncMock(return_value=self._client)
        self._client.__aexit__ = AsyncMock(return_value=False)
        self._client.invoke = AsyncMock()
        self._proxy_calls = 0

    def _normalize_account_name(self, name):
        return name

    def account_exists(self, name):
        return True

    def _build_account_client(self, name, no_updates=True):
        return self._client, None

    async def verify_account_proxy(self, name, proxy_dict=None):
        self._proxy_calls += 1


def _authz_result(ttl_days):
    return SimpleNamespace(authorization_ttl_days=ttl_days, authorizations=[])


def _account_ttl_result(days):
    return SimpleNamespace(days=days)


@pytest.mark.asyncio
async def test_get_session_settings_reads_both_ttls():
    svc = DummyDevicesService()
    svc._client.invoke = AsyncMock(
        side_effect=[_authz_result(180), _account_ttl_result(540)]
    )

    result = await svc.get_session_settings("acc1")

    assert result == {
        "authorization_ttl_days": 180,
        "account_ttl_days": 540,
    }
    assert svc._client.invoke.await_count == 2


@pytest.mark.asyncio
async def test_set_session_settings_writes_and_rereads():
    from pyrogram import raw

    svc = DummyDevicesService()
    svc._client.invoke = AsyncMock(
        side_effect=[
            SimpleNamespace(),  # SetAuthorizationTTL
            SimpleNamespace(),  # SetAccountTTL
            _authz_result(90),  # re-read GetAuthorizations
            _account_ttl_result(365),  # re-read GetAccountTTL
        ]
    )

    result = await svc.set_session_settings(
        "acc1", authorization_ttl_days=90, account_ttl_days=365
    )

    assert result == {
        "authorization_ttl_days": 90,
        "account_ttl_days": 365,
    }
    calls = [c.args[0] for c in svc._client.invoke.await_args_list]
    assert isinstance(calls[0], raw.functions.account.SetAuthorizationTTL)
    assert calls[0].authorization_ttl_days == 90
    assert isinstance(calls[1], raw.functions.account.SetAccountTTL)
    assert calls[1].ttl.days == 365


@pytest.mark.asyncio
async def test_set_session_settings_fresh_login_406_mapped():
    """当前会话登录不足 24h 时 Telegram 拒写（406），映射为明确业务码。"""
    svc = DummyDevicesService()
    svc._client.invoke = AsyncMock(
        side_effect=Exception(
            "406 FRESH_RESET_AUTHORISATION_FORBIDDEN: You can't terminate "
            "other authorized sessions because the current was logged-in "
            "recently, please wait at least 24 hours. "
            '(caused by "account.SetAuthorizationTTL")'
        )
    )
    with pytest.raises(ValueError, match="SESSION_TTL_FRESH_LOGIN"):
        await svc.set_session_settings("acc1", authorization_ttl_days=90)


@pytest.mark.asyncio
async def test_set_session_settings_empty_rejected():
    svc = DummyDevicesService()
    with pytest.raises(ValueError, match="SESSION_SETTINGS_EMPTY"):
        await svc.set_session_settings("acc1")


@pytest.mark.asyncio
async def test_set_session_settings_range_rejected():
    svc = DummyDevicesService()
    with pytest.raises(ValueError, match="AUTHORIZATION_TTL_INVALID"):
        await svc.set_session_settings("acc1", authorization_ttl_days=3)
    with pytest.raises(ValueError, match="ACCOUNT_TTL_INVALID"):
        await svc.set_session_settings("acc1", account_ttl_days=800)
    assert svc._client.invoke.await_count == 0


def test_session_settings_routes(api_client, db):  # noqa: F811
    token = _login(api_client)
    mock_svc = MagicMock()
    mock_svc.get_session_settings = AsyncMock(
        return_value={"authorization_ttl_days": 180, "account_ttl_days": 540}
    )
    mock_svc.set_session_settings = AsyncMock(
        return_value={"authorization_ttl_days": 30, "account_ttl_days": 365}
    )

    with patch(
        "backend.api.routes.accounts.get_telegram_service", return_value=mock_svc
    ):
        resp = api_client.get(
            "/api/accounts/acc1/session-settings", headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "authorization_ttl_days": 180,
            "account_ttl_days": 540,
        }
        assert resp.headers["cache-control"] == "no-store"
        mock_svc.get_session_settings.assert_awaited_once_with("acc1")

        resp = api_client.put(
            "/api/accounts/acc1/session-settings",
            json={"authorization_ttl_days": 30, "account_ttl_days": 365},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "authorization_ttl_days": 30,
            "account_ttl_days": 365,
        }
        mock_svc.set_session_settings.assert_awaited_once_with(
            "acc1", authorization_ttl_days=30, account_ttl_days=365
        )

        # 超范围 → 400（pydantic validator）
        resp = api_client.put(
            "/api/accounts/acc1/session-settings",
            json={"authorization_ttl_days": 3},
            headers=_auth(token),
        )
        assert resp.status_code == 422, resp.text

        # 两项都空 → 400 SESSION_SETTINGS_EMPTY（service 层）
        mock_svc.set_session_settings = AsyncMock(
            side_effect=ValueError("SESSION_SETTINGS_EMPTY")
        )
        resp = api_client.put(
            "/api/accounts/acc1/session-settings",
            json={},
            headers=_auth(token),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "SESSION_SETTINGS_EMPTY"

        # 406 新登录限制 → 400 SESSION_TTL_FRESH_LOGIN（service 层映射）
        mock_svc.set_session_settings = AsyncMock(
            side_effect=ValueError("SESSION_TTL_FRESH_LOGIN")
        )
        resp = api_client.put(
            "/api/accounts/acc1/session-settings",
            json={"authorization_ttl_days": 90},
            headers=_auth(token),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "SESSION_TTL_FRESH_LOGIN"
