"""代理池 API 路由冒烟测试：挂载、CRUD、路由绑定、全局代理、订阅设置。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.utils.tg_session import set_account_profile

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    """lifespan 建 admin 时用的密码（与 test_api.py 同款）。"""
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)


@pytest.fixture(autouse=True)
def _reset_telegram_service(client):
    """每个测试用独立临时目录：TelegramService 单例在首次创建时就钉死了
    session_dir，runtime.py 模块级 settings 也在首次 import 时钉死；
    必须在 client 启动后两者一起刷新，让服务按当前测试的目录重建。

    注意不能在 client 启动前 import runtime：模块级 settings 会在
    APP_DATA_DIR 生效前就按默认值钉死（/data/sessions）。
    """
    import backend.services.telegram.runtime as _runtime
    from backend.core.config import get_settings

    _runtime.settings = get_settings()
    _runtime._telegram_service = None
    yield
    _runtime._telegram_service = None


def _login(client: TestClient) -> dict:
    resp = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_proxies_crud_and_list_hides_secrets(client: TestClient):
    headers = _login(client)
    resp = client.post(
        "/api/proxies",
        json={
            "ptype": "socks5",
            "host": "1.2.3.4",
            "port": 1080,
            "username": "alice",
            "password": "s3cret-pw",
            "label": "manual-one",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["success"] is True

    resp = client.get("/api/proxies", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    item = body["proxies"][0]
    assert item["has_password"] is True
    assert "s3cret-pw" not in resp.text
    assert "alice" not in item["display_url"]

    # 密码/密钥字段根本不在响应里
    assert "password" not in item
    assert "secret" not in item

    # 改名 + 删除
    proxy_id = item["id"]
    resp = client.post(
        f"/api/proxies/{proxy_id}/rename",
        json={"label": "renamed"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["proxy"]["label"] == "renamed"
    resp = client.delete(f"/api/proxies/{proxy_id}", headers=headers)
    assert resp.status_code == 200
    assert client.get("/api/proxies", headers=headers).json()["total"] == 0


def test_proxies_import_and_mtproto_validation(client: TestClient, monkeypatch):
    # 导入时做 Telegram 探测：测试环境没有真实网络，用 mock 让探测通过。
    from backend.utils.proxy import ProxyProbeOutcome, ProxyProbeStatus

    async def _probe_ok(*args, **kwargs):
        return ProxyProbeOutcome(status=ProxyProbeStatus.OK, detail="ok", code="OK")

    monkeypatch.setattr("backend.utils.proxy.probe_proxy_exit", _probe_ok)
    headers = _login(client)
    resp = client.post(
        "/api/proxies/import",
        json={
            "text": "socks5://user:pass@1.2.3.4:1080\n"
            "tg://proxy?server=5.6.7.8&port=443&secret=dd0123456789abcdef0123456789abcdef\n"
            "1.2.3.4:1080\n"
            "not a proxy\n"
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 2
    assert body["skipped"] == 2  # 重复行 + 无效行
    assert body["telegram_failed"] == 0

    # MTProto 不带 secret 拒绝
    resp = client.post(
        "/api/proxies",
        json={"ptype": "mtproto", "host": "5.6.7.8", "port": 443},
        headers=headers,
    )
    assert resp.status_code == 400


def test_proxies_import_rejects_telegram_unreachable(client: TestClient, monkeypatch):
    # TCP 通但 Telegram 握手失败的，导入时直接剔除，不入库。
    from backend.utils.proxy import ProxyProbeOutcome, ProxyProbeStatus

    async def _probe_fail(*args, **kwargs):
        return ProxyProbeOutcome(
            status=ProxyProbeStatus.UNAVAILABLE,
            detail="handshake failed",
            code="PROXY_PROBE_UNAVAILABLE",
        )

    monkeypatch.setattr("backend.utils.proxy.probe_proxy_exit", _probe_fail)
    headers = _login(client)
    resp = client.post(
        "/api/proxies/import",
        json={"text": "socks5://user:pass@1.2.3.4:1080\n"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 0
    assert body["telegram_failed"] == 1
    assert client.get("/api/proxies", headers=headers).json()["total"] == 0


def test_proxies_routes_and_global(client: TestClient):
    headers = _login(client)
    set_account_profile("acct1", remark="smoke")

    resp = client.get("/api/proxies/routes", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["routes"]) == 1
    assert body["routes"][0]["account_name"] == "acct1"
    assert body["routes"][0]["mode"] == "inherit"
    assert body["global_proxy"]["set"] is False

    # 全局代理：设置 → 打码展示 → 清空
    resp = client.post(
        "/api/proxies/global",
        json={"proxy_url": "socks5://1.2.3.4:1080"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resp = client.get("/api/proxies/global", headers=headers)
    assert resp.json()["set"] is True
    assert resp.json()["display_url"] == "socks5://1.2.3.4:1080"
    resp = client.post("/api/proxies/global", json={"proxy_url": None}, headers=headers)
    assert resp.status_code == 200
    assert client.get("/api/proxies/global", headers=headers).json()["set"] is False

    # 路由绑定：直连
    resp = client.post(
        "/api/proxies/routes",
        json={"account_name": "acct1", "mode": "direct"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resp = client.get("/api/proxies/routes", headers=headers)
    assert resp.json()["routes"][0]["mode"] == "direct"

    # 指定不存在的代理 → 400
    resp = client.post(
        "/api/proxies/routes",
        json={"account_name": "acct1", "mode": "pool", "proxy_id": "nope123"},
        headers=headers,
    )
    assert resp.status_code == 400


def test_proxies_subscription_setting(client: TestClient):
    headers = _login(client)
    resp = client.get("/api/proxies/subscription", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_url"].startswith("https://freeproxydb.com/")
    assert resp.json()["custom"] is False

    resp = client.post(
        "/api/proxies/subscription",
        json={"url": "https://example.com/sub"},
        headers=headers,
    )
    assert resp.status_code == 200
    resp = client.get("/api/proxies/subscription", headers=headers)
    assert resp.json()["url"] == "https://example.com/sub"
    assert resp.json()["custom"] is True

    # 传空恢复默认
    client.post("/api/proxies/subscription", json={"url": ""}, headers=headers)
    resp = client.get("/api/proxies/subscription", headers=headers)
    assert resp.json()["custom"] is False


def test_accounts_list_enriched_fields(client: TestClient):
    headers = _login(client)
    set_account_profile("acct1", remark="smoke")
    # 账号列表以 session 文件为准：放一个占位 .session 文件（只 stat，不解析）
    from backend.core.config import get_settings

    session_dir = get_settings().resolve_session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "acct1.session").write_bytes(b"dummy-session")
    resp = client.get("/api/accounts", headers=headers)
    assert resp.status_code == 200, resp.text
    accounts = resp.json()["accounts"]
    assert len(accounts) == 1
    item = accounts[0]
    # 新字段都在响应里（缺失值留空，不伪造）
    for key in (
        "user_id",
        "dc_id",
        "dc_location",
        "phone",
        "username",
        "first_name",
        "proxy_mode",
        "proxy_id",
        "proxy_label",
        "added_at",
    ):
        assert key in item
    assert item["proxy_mode"] == "inherit"
    assert item["added_at"] is not None  # 本次新建条目刚盖了时间戳
