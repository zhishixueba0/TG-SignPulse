"""代理池存储测试：密钥不外泄、去重、中伊规则、绑定解析。"""

from __future__ import annotations

import pytest

from backend.services.telegram import proxy_store
from backend.services.telegram.proxy_geo import is_blocked_geo
from backend.utils.tg_session import set_account_profile


@pytest.fixture(autouse=True)
def _reset_proxy_jobs():
    yield
    proxy_store._sync_job.update({"status": "idle"})
    proxy_store._test_all_job.update({"status": "idle"})
    proxy_store._cleanup_job.update({"status": "idle"})


def test_secret_never_leaked_in_serialization(memory_session_local):
    # dd + 32 hex = 17 字节合法 secret（ee 需另带 SNI 域名才合法）
    row = proxy_store.create_proxy(
        ptype="mtproto",
        host="1.2.3.4",
        port=443,
        secret="dd0123456789abcdef0123456789abcdef",
        label="test",
    )
    data = proxy_store.serialize_proxy(row)
    dumped = str(data)
    assert "dd0123456789abcdef0123456789abcdef" not in dumped
    assert data["has_secret"] is True
    assert data["has_password"] is False
    assert data["display_url"].startswith("mtproto://1.2.3.4:443")
    assert "dd" not in data["display_url"]


def test_password_never_leaked(memory_session_local):
    row = proxy_store.create_proxy(
        ptype="socks5",
        host="1.2.3.4",
        port=1080,
        username="alice",
        password="s3cret-pw",
        label="pw",
    )
    data = proxy_store.serialize_proxy(row)
    assert "s3cret-pw" not in str(data)
    assert data["has_password"] is True
    assert "alice" not in data["display_url"]
    assert "s3cret-pw" not in data["display_url"]


def test_mtproto_without_secret_rejected(memory_session_local):
    with pytest.raises(ValueError):
        proxy_store.create_proxy(ptype="mtproto", host="1.2.3.4", port=443)


def test_dedupe_same_address(memory_session_local):
    proxy_store.create_proxy(ptype="socks5", host="1.2.3.4", port=1080, label="a")
    with pytest.raises(ValueError):
        proxy_store.create_proxy(ptype="socks5", host="1.2.3.4", port=1080, label="b")
    assert len(proxy_store.list_proxy_rows()) == 1


def test_cn_ir_blocked_rule():
    assert is_blocked_geo({"country_code": "CN", "reg_country": "US"})
    assert is_blocked_geo({"country_code": "US", "reg_country": "CN"})
    assert is_blocked_geo({"country_code": "IR", "reg_country": None})
    assert is_blocked_geo({"country_code": None, "reg_country": "IR"})
    assert not is_blocked_geo({"country_code": "US", "reg_country": "US"})
    assert not is_blocked_geo({"country_code": None, "reg_country": None})


def test_binding_modes(memory_session_local, isolated_env):
    row = proxy_store.create_proxy(
        ptype="socks5", host="5.6.7.8", port=1080, label="pool-one"
    )
    # 默认继承全局（测试环境未设全局代理，显示带“未设置”后缀）
    binding = proxy_store.get_account_proxy_binding("acct1")
    assert binding["mode"] == "inherit"
    assert binding["effective_label"].startswith("继承全局代理")

    # 指定池子代理
    set_account_profile("acct1", proxy_mode="pool", proxy_id=row.id)
    binding = proxy_store.get_account_proxy_binding("acct1")
    assert binding["mode"] == "pool"
    assert binding["proxy_id"] == row.id
    assert binding["proxy_label"] == "pool-one"
    url = proxy_store.resolve_account_proxy_url("acct1")
    assert url == "socks5://5.6.7.8:1080"

    # 直连
    set_account_profile("acct1", proxy_mode="direct", proxy_id=None)
    assert proxy_store.resolve_account_proxy_url("acct1") is None

    # 自定义 URL
    set_account_profile("acct1", proxy_mode="custom", proxy="socks5://9.9.9.9:1080")
    assert proxy_store.resolve_account_proxy_url("acct1") == "socks5://9.9.9.9:1080"

    # 老账号：只有代理字符串 → 推断为自定义
    set_account_profile("acct2", proxy="http://3.3.3.3:8080")
    binding = proxy_store.get_account_proxy_binding("acct2")
    assert binding["mode"] == "custom"
    assert proxy_store.resolve_account_proxy_url("acct2") == "http://3.3.3.3:8080"


def test_deleted_pool_proxy_resets_account_to_direct(
    memory_session_local, isolated_env
):
    row = proxy_store.create_proxy(
        ptype="socks5", host="5.6.7.8", port=1080, label="pool-one"
    )
    set_account_profile("acct1", proxy_mode="pool", proxy_id=row.id)
    reset = proxy_store.delete_proxy_row(row.id)
    assert "acct1" in reset
    binding = proxy_store.get_account_proxy_binding("acct1")
    assert binding["mode"] == "direct"
    assert proxy_store.resolve_account_proxy_url("acct1") is None


def test_canonical_url_roundtrip(memory_session_local):
    row = proxy_store.create_proxy(
        ptype="mtproto",
        host="1.2.3.4",
        port=443,
        secret="dd0123456789abcdef0123456789abcdef",
        label="mt",
    )
    url = proxy_store.canonical_url(row)
    assert url.startswith("mtproto://dd0123456789abcdef0123456789abcdef@1.2.3.4:443")
    proxy_dict = proxy_store.proxy_dict_for_row(row)
    assert proxy_dict is not None
    assert proxy_dict["scheme"] == "mtproxy"


@pytest.mark.asyncio
async def test_sync_skips_invalid_mtproto_secret(memory_session_local, monkeypatch):
    """订阅源里混进一个非法 MTProto secret 时，整轮同步不能报错中断。

    回归：freeproxydb 曾返回过解不出 16 字节的 secret，create_proxy 抛
    ValueError 后 run_proxy_sync 整轮标 error，后面的补归属地/中伊剔除/
    过期清理全都没跑。非法条目只跳过，好条目照常入库，job 正常 done。
    """

    async def fake_fetch(url):
        return "\n".join(
            [
                "socks5://127.0.0.1:1080",
                "tg://proxy?server=1.2.3.4&port=443&secret=ZZZNOTHEX",
                "tg://proxy?server=5.6.7.8&port=443&secret=0123456789abcdef0123456789abcdef",
            ]
        )

    async def fake_tcp(host, port, timeout=3.0):
        return (True, 10)

    async def fake_enrich(proxy_id):
        return {}

    async def fake_enrich_unknown(limit=100):
        return 0

    monkeypatch.setattr(proxy_store, "_fetch_subscription_text", fake_fetch)
    monkeypatch.setattr(proxy_store, "tcp_test_async", fake_tcp)
    monkeypatch.setattr(proxy_store, "enrich_proxy", fake_enrich)
    monkeypatch.setattr(proxy_store, "enrich_unknown_proxies", fake_enrich_unknown)

    job = await proxy_store.run_proxy_sync()

    assert job["status"] == "done"
    assert job["error"] is None
    assert job["added"] == 2
    rows = proxy_store.list_proxy_rows()
    assert {r.host for r in rows} == {"127.0.0.1", "5.6.7.8"}
