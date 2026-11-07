"""mark_client_online：登录瞬间刷在线状态（best-effort，失败不影响登录）。"""

from unittest.mock import AsyncMock

import pytest

from backend.utils.tg_session import mark_client_online


class _FakeClient:
    def __init__(self, connected=True, has_method=True):
        self.is_connected = connected
        self.calls = []
        if has_method:
            self.update_status = AsyncMock(side_effect=self._record)

    async def _record(self, offline=False):
        self.calls.append(offline)
        return True


@pytest.mark.asyncio
async def test_mark_online_calls_update_status_offline_false():
    client = _FakeClient()
    assert await mark_client_online(client, "12098814721") is True
    assert client.calls == [False]


@pytest.mark.asyncio
async def test_mark_online_skips_disconnected_client():
    client = _FakeClient(connected=False)
    assert await mark_client_online(client, "12098814721") is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_mark_online_never_raises():
    client = _FakeClient()
    client.update_status = AsyncMock(side_effect=RuntimeError("boom"))
    assert await mark_client_online(client, "12098814721") is False


@pytest.mark.asyncio
async def test_mark_online_missing_method_is_false():
    client = _FakeClient(has_method=False)
    assert await mark_client_online(client, "12098814721") is False
