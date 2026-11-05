"""第二轮账号管理：自定义顺序 / 暂停 / 刷新资料 / 聊天路由 / 批量检测过滤。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.tg_session import get_account_profile, set_account_profile
from tests.test_api import _auth, _login, api_client, db  # noqa: F401  (fixtures)


def _write_store(session_dir: Path, entries: dict) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "accounts.json").write_text(
        json.dumps({"accounts": entries}, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def svc(isolated_env: Path, monkeypatch):
    """指向临时数据目录的真实 TelegramService（只用本地文件，不连 Telegram）。"""
    from backend.core.config import get_settings
    from backend.services.telegram.runtime import TelegramService

    service = TelegramService()
    # runtime 模块级 settings 在 import 时绑定，用当前解析结果覆盖
    service.session_dir = get_settings().resolve_session_dir()
    service.session_dir.mkdir(parents=True, exist_ok=True)
    yield service


def _make_session(svc, name: str) -> None:
    (svc.session_dir / f"{name}.session").write_bytes(b"x" * 16)


class TestProfileOrderPause:
    def test_new_account_gets_max_plus_one(self, isolated_env: Path):
        set_account_profile("acc_a", remark="a")
        set_account_profile("acc_b", remark="b")
        assert get_account_profile("acc_a")["sort_order"] == 0
        assert get_account_profile("acc_b")["sort_order"] == 1
        assert get_account_profile("acc_a")["paused"] is False

    def test_explicit_sort_order_and_pause_persist(self, isolated_env: Path):
        set_account_profile("acc", sort_order=5, paused=True)
        prof = get_account_profile("acc")
        assert prof["sort_order"] == 5
        assert prof["paused"] is True
        # 只改备注不覆盖顺序与暂停
        set_account_profile("acc", remark="r")
        prof = get_account_profile("acc")
        assert prof["sort_order"] == 5
        assert prof["paused"] is True
        set_account_profile("acc", paused=False)
        assert get_account_profile("acc")["paused"] is False


class TestServiceOrderMove:
    def test_backfill_old_accounts_in_current_order(self, svc):
        for name in ("b", "a", "c"):
            _make_session(svc, name)
        # 老账号条目缺 sort_order：直接写 store 模拟
        _write_store(
            svc.session_dir,
            {name: {"remark": name} for name in ("a", "b", "c")},
        )
        entries = svc.list_accounts(force_refresh=True)
        assert [e["name"] for e in entries] == ["a", "b", "c"]
        assert [e["sort_order"] for e in entries] == [0, 1, 2]
        # 已落盘：下次读取不再回填
        assert get_account_profile("a")["sort_order"] == 0

    def test_move_swaps_and_edges(self, svc):
        for name in ("a", "b", "c"):
            _make_session(svc, name)
            set_account_profile(name, sort_order={"a": 0, "b": 1, "c": 2}[name])
        res = svc.move_account("b", "up")
        assert res["moved"] is True
        assert [e["name"] for e in svc.list_accounts(force_refresh=True)] == [
            "b",
            "a",
            "c",
        ]
        res = svc.move_account("b", "up")
        assert res["moved"] is False
        assert "最上面" in res["message"]
        res = svc.move_account("c", "down")
        assert res["moved"] is False
        assert "最下面" in res["message"]
        res = svc.move_account("a", "down")
        assert res["moved"] is True
        assert [e["name"] for e in svc.list_accounts(force_refresh=True)] == [
            "b",
            "c",
            "a",
        ]

    def test_move_bad_direction_and_missing(self, svc):
        _make_session(svc, "a")
        set_account_profile("a", sort_order=0)
        with pytest.raises(ValueError, match="direction"):
            svc.move_account("a", "left")
        with pytest.raises(ValueError, match="ACCOUNT_NOT_FOUND"):
            svc.move_account("nope", "up")

    def test_pause_roundtrip(self, svc):
        _make_session(svc, "a")
        entry = svc.set_account_paused("a", True)
        assert entry["paused"] is True
        assert get_account_profile("a")["paused"] is True
        entry = svc.set_account_paused("a", False)
        assert entry["paused"] is False
        with pytest.raises(ValueError, match="ACCOUNT_NOT_FOUND"):
            svc.set_account_paused("nope", True)


class TestRefreshAccountProfile:
    @pytest.mark.asyncio
    async def test_refresh_updates_identity(self, svc):
        _make_session(svc, "a")
        fake_me = SimpleNamespace(
            id=7, first_name="F", username="u", phone="+10000000001"
        )
        fake_client = MagicMock()
        fake_client.is_connected = True
        fake_client.get_me = AsyncMock(return_value=fake_me)
        with (
            patch.object(svc, "_build_account_client", return_value=(fake_client, {})),
            patch.object(svc, "verify_account_proxy", new=AsyncMock(return_value=None)),
        ):
            entry = await svc.refresh_account_profile("a")
        assert entry["user_id"] == 7
        assert entry["first_name"] == "F"
        assert entry["username"] == "u"
        assert entry["phone"] == "+10000000001"

    @pytest.mark.asyncio
    async def test_refresh_invalid_session_401_no_identity_change(self, svc):
        _make_session(svc, "a")
        set_account_profile("a", remark="keep")
        fake_client = MagicMock()
        fake_client.is_connected = True
        fake_client.get_me = AsyncMock(
            side_effect=RuntimeError("AUTH_KEY_UNREGISTERED")
        )
        with (
            patch.object(svc, "_build_account_client", return_value=(fake_client, {})),
            patch.object(svc, "verify_account_proxy", new=AsyncMock(return_value=None)),
            patch(
                "backend.services.telegram.accounts.is_invalid_session_error",
                return_value=True,
            ),
        ):
            with pytest.raises(ValueError, match="ACCOUNT_SESSION_INVALID"):
                await svc.refresh_account_profile("a")
        # 身份字段未被改写
        assert get_account_profile("a").get("user_id") is None


class TestAccountRoutes:
    def test_move_route(self, api_client, db):  # noqa: F811
        token = _login(api_client)
        svc = MagicMock()
        svc.move_account.return_value = {
            "moved": True,
            "message": "已移动",
            "account": {
                "name": "a1",
                "session_file": "a1.session",
                "exists": True,
                "size": 1,
            },
        }
        with patch(
            "backend.api.routes.accounts.get_telegram_service", return_value=svc
        ):
            resp = api_client.post(
                "/api/accounts/a1/move",
                json={"direction": "up"},
                headers=_auth(token),
            )
        assert resp.status_code == 200
        assert resp.json()["moved"] is True

    def test_pause_resume_routes(self, api_client, db):  # noqa: F811
        token = _login(api_client)
        svc = MagicMock()
        svc.set_account_paused.side_effect = [
            {
                "name": "a1",
                "session_file": "a1.session",
                "exists": True,
                "size": 1,
                "paused": True,
            },
            {
                "name": "a1",
                "session_file": "a1.session",
                "exists": True,
                "size": 1,
                "paused": False,
            },
        ]
        with patch(
            "backend.api.routes.accounts.get_telegram_service", return_value=svc
        ):
            paused = api_client.post("/api/accounts/a1/pause", headers=_auth(token))
            resumed = api_client.post("/api/accounts/a1/resume", headers=_auth(token))
        assert paused.status_code == 200
        assert paused.json()["account"]["paused"] is True
        assert resumed.status_code == 200
        assert resumed.json()["account"]["paused"] is False

    def test_refresh_profile_route_codes(self, api_client, db):  # noqa: F811
        token = _login(api_client)
        svc = MagicMock()
        svc.refresh_account_profile = AsyncMock(
            return_value={
                "name": "a1",
                "session_file": "a1.session",
                "exists": True,
                "size": 1,
            }
        )
        with patch(
            "backend.api.routes.accounts.get_telegram_service", return_value=svc
        ):
            ok = api_client.post(
                "/api/accounts/a1/refresh-profile", headers=_auth(token)
            )
        assert ok.status_code == 200

        svc.refresh_account_profile = AsyncMock(
            side_effect=ValueError("ACCOUNT_SESSION_INVALID: bad")
        )
        with patch(
            "backend.api.routes.accounts.get_telegram_service", return_value=svc
        ):
            invalid = api_client.post(
                "/api/accounts/a1/refresh-profile", headers=_auth(token)
            )
        assert invalid.status_code == 401

        svc.refresh_account_profile = AsyncMock(
            side_effect=ValueError("ACCOUNT_NOT_FOUND")
        )
        with patch(
            "backend.api.routes.accounts.get_telegram_service", return_value=svc
        ):
            missing = api_client.post(
                "/api/accounts/a1/refresh-profile", headers=_auth(token)
            )
        assert missing.status_code == 404


class TestChatsRoutes:
    def test_dialogs_messages_send(self, api_client, db):  # noqa: F811
        token = _login(api_client)
        svc = MagicMock()
        svc.list_account_dialogs = AsyncMock(
            return_value=[
                {
                    "chat_id": 123,
                    "title": "Alice",
                    "type": "private",
                    "username": "alice",
                    "unread_count": 2,
                    "last_message_text": "hi",
                    "last_message_date": "2026-11-05T10:00:00Z",
                }
            ]
        )
        svc.list_chat_messages = AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "date": "2026-11-05T10:00:00Z",
                    "text": "hi",
                    "outgoing": False,
                    "sender_name": "Alice",
                    "sender_id": 123,
                }
            ]
        )
        svc.send_chat_message = AsyncMock(
            return_value={
                "id": 2,
                "date": "2026-11-05T10:01:00Z",
                "text": "hello",
                "outgoing": True,
                "sender_name": "",
                "sender_id": None,
            }
        )
        with patch("backend.api.routes.chats.get_telegram_service", return_value=svc):
            dialogs = api_client.get(
                "/api/chats/dialogs?account=a1", headers=_auth(token)
            )
            messages = api_client.get(
                "/api/chats/a1/messages?chat_id=123", headers=_auth(token)
            )
            sent = api_client.post(
                "/api/chats/a1/messages",
                json={"chat_id": 123, "text": "hello"},
                headers=_auth(token),
            )
        assert dialogs.status_code == 200
        assert dialogs.json()["total"] == 1
        assert dialogs.json()["dialogs"][0]["title"] == "Alice"
        assert messages.status_code == 200
        assert messages.json()["messages"][0]["text"] == "hi"
        assert sent.status_code == 200
        assert sent.json()["message"]["outgoing"] is True
        svc.send_chat_message.assert_awaited_once_with("a1", 123, "hello")

    def test_dialogs_missing_account_404(self, api_client, db):  # noqa: F811
        token = _login(api_client)
        svc = MagicMock()
        svc.list_account_dialogs = AsyncMock(side_effect=ValueError("账号不存在"))
        with patch("backend.api.routes.chats.get_telegram_service", return_value=svc):
            resp = api_client.get(
                "/api/chats/dialogs?account=nope", headers=_auth(token)
            )
        assert resp.status_code == 404


class TestPausedBatchCheckFilter:
    def test_normalize_names_skips_paused(self, monkeypatch):
        from backend.services import account_status_jobs as jobs_mod

        with patch("backend.services.telegram.get_telegram_service") as mock_get:
            mock_get.return_value.list_accounts.return_value = []
            with patch(
                "backend.utils.tg_session.get_account_profile",
                side_effect=lambda name: {"paused": name == "a"},
            ):
                assert jobs_mod._normalize_names(["a", "b"]) == ["b"]
            with patch(
                "backend.utils.tg_session.get_account_profile",
                return_value={"paused": True},
            ):
                with pytest.raises(ValueError, match="没有可检测的账号"):
                    jobs_mod._normalize_names(["a"])


class TestRunnerPausedSkip:
    @pytest.mark.asyncio
    async def test_check_account_skips_paused(self, monkeypatch):
        from backend.services import sign_task_runner as runner_mod

        svc = MagicMock()
        state: dict = {
            "svc": svc,
            "account_name": "a1",
            "task_name": "t1",
            "task_key": "a1:t1",
        }
        monkeypatch.setattr(
            "backend.utils.tg_session.get_account_profile",
            lambda name: {"paused": True},
        )
        await runner_mod._runner_check_account(state)
        assert state.get("account_paused") is True
        assert "已暂停" in str(state.get("error_msg") or "")
        svc._append_active_log.assert_called_once()
