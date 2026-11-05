from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.core.config import get_settings
from backend.services.config import ConfigService
from backend.services.proxy_circuit_breaker import (
    CircuitState,
    get_proxy_circuit_breaker,
)
from backend.services.telegram.accounts import TelegramAccountsMixin
from backend.utils.proxy import (
    DEFAULT_PROBE_ENDPOINTS,
    ProxyProbeStatus,
    clear_proxy_probe_cache,
    probe_proxy_exit,
)
from backend.utils.proxy_handshake import HandshakeResult
from backend.utils.tg_session import _account_store_cache, set_account_profile


def _handshake_ok():
    """Mock the Telegram handshake screen as passed (3ms)."""
    from unittest.mock import AsyncMock

    return AsyncMock(
        return_value=HandshakeResult(ok=True, code="OK", message="OK", latency_ms=3)
    )


@pytest.fixture(autouse=True)
def reset_cache():
    clear_proxy_probe_cache()
    _account_store_cache.clear()
    get_settings.cache_clear()
    yield
    clear_proxy_probe_cache()
    _account_store_cache.clear()
    get_settings.cache_clear()


class DummyTelegramService(TelegramAccountsMixin):
    def __init__(self, session_dir: Path, workdir: Path):
        self.session_dir = session_dir
        self.workdir = workdir
        self._accounts_cache = None


@pytest.mark.asyncio
async def test_global_require_proxy_blocks_unproxied_account(tmp_path, monkeypatch):
    """全局开启 require_proxy_for_telegram 时，无代理账号在 _build_account_client / 门禁前被硬阻断。"""
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True)
    workdir = tmp_path / "data"
    workdir.mkdir(parents=True)

    session_file = session_dir / "unproxied_acc.session"
    session_file.write_text("dummy")

    cfg_svc = ConfigService()
    cfg_svc.save_global_settings(
        {"require_proxy_for_telegram": True, "global_proxy": None}
    )

    tg_svc = DummyTelegramService(session_dir, workdir)

    with patch("backend.services.config.get_config_service", return_value=cfg_svc):
        with pytest.raises(RuntimeError, match="PROXY_REQUIRED_BLOCKED"):
            tg_svc._build_account_client("unproxied_acc")

        with pytest.raises(RuntimeError, match="PROXY_REQUIRED_BLOCKED"):
            await tg_svc.verify_account_proxy("unproxied_acc")

        with pytest.raises(RuntimeError, match="PROXY_REQUIRED_BLOCKED"):
            await tg_svc.download_account_avatar("unproxied_acc")

        with pytest.raises(RuntimeError, match="PROXY_REQUIRED_BLOCKED"):
            await tg_svc.download_chat_avatar("unproxied_acc", 12345)


@pytest.mark.asyncio
async def test_probe_proxy_failed_when_exit_matches_host_ip(tmp_path, monkeypatch):
    """当探针探测到代理出口 IP 等于宿主机直连出口 IP 时，判定为 PROXY_PROBE_FAILED 并硬熔断。"""
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True)
    workdir = tmp_path / "data"
    workdir.mkdir(parents=True)

    session_file = session_dir / "leaking_acc.session"
    session_file.write_text("dummy")

    proxy_dict = {"scheme": "socks5", "hostname": "127.0.0.1", "port": 1080}
    set_account_profile("leaking_acc", proxy="socks5://127.0.0.1:1080")

    host_ip = "198.51.100.42"

    # probe_proxy_exit 直接调用验证（握手快筛 mock 为通过，出口 IP 走新版
    # _fetch_ip_via_proxy_detailed；两端点并行，各调一次）
    with (
        patch(
            "backend.utils.proxy._fetch_ip_via_proxy_detailed",
            new_callable=AsyncMock,
        ) as mock_proxy_ip,
        patch(
            "backend.utils.proxy.get_host_direct_ip", new_callable=AsyncMock
        ) as mock_host_ip,
        patch(
            "backend.utils.proxy.probe_telegram_handshake",
            new=_handshake_ok(),
        ),
    ):
        mock_host_ip.return_value = host_ip
        mock_proxy_ip.return_value = (host_ip, None)  # 泄漏：出口 IP 与宿主机一致

        status, ip = await probe_proxy_exit(proxy_dict)
        assert status == ProxyProbeStatus.FAILED
        assert ip == host_ip

    # 门禁调用验证：即使配置 policy="warn"，FAILED 也必须无条件硬熔断
    set_account_profile(
        "leaking_acc", proxy="socks5://127.0.0.1:1080", proxy_probe_policy="warn"
    )
    cfg_svc = ConfigService()

    tg_svc = DummyTelegramService(session_dir, workdir)
    with (
        patch("backend.services.config.get_config_service", return_value=cfg_svc),
        patch(
            "backend.utils.proxy.probe_proxy_exit", new_callable=AsyncMock
        ) as mock_probe,
    ):
        mock_probe.return_value = (ProxyProbeStatus.FAILED, host_ip)
        with pytest.raises(
            RuntimeError, match="PROXY_PROBE_FAILED: Proxy leaks host IP"
        ):
            await tg_svc.verify_account_proxy("leaking_acc", proxy_dict)


@pytest.mark.asyncio
async def test_probe_proxy_unavailable_strict_blocks(tmp_path, monkeypatch):
    """当探针不可达且账号策略为 strict（默认）时，触发阻断。"""
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True)
    workdir = tmp_path / "data"
    workdir.mkdir(parents=True)

    session_file = session_dir / "strict_acc.session"
    session_file.write_text("dummy")

    proxy_dict = {"scheme": "socks5", "hostname": "127.0.0.1", "port": 1080}
    set_account_profile(
        "strict_acc", proxy="socks5://127.0.0.1:1080", proxy_probe_policy="strict"
    )

    tg_svc = DummyTelegramService(session_dir, workdir)
    cfg_svc = ConfigService()

    with (
        patch("backend.services.config.get_config_service", return_value=cfg_svc),
        patch(
            "backend.utils.proxy.probe_proxy_exit", new_callable=AsyncMock
        ) as mock_probe,
    ):
        mock_probe.return_value = (ProxyProbeStatus.UNAVAILABLE, "Connection timed out")
        with pytest.raises(RuntimeError, match="PROXY_PROBE_UNAVAILABLE"):
            await tg_svc.verify_account_proxy("strict_acc", proxy_dict)


@pytest.mark.asyncio
async def test_probe_proxy_unavailable_warn_policy_allows(tmp_path, monkeypatch):
    """当探针不可达但账号策略为 warn 时，记录警告并放行。"""
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True)
    workdir = tmp_path / "data"
    workdir.mkdir(parents=True)

    session_file = session_dir / "warn_acc.session"
    session_file.write_text("dummy")

    proxy_dict = {"scheme": "socks5", "hostname": "127.0.0.1", "port": 1080}
    set_account_profile(
        "warn_acc", proxy="socks5://127.0.0.1:1080", proxy_probe_policy="warn"
    )

    tg_svc = DummyTelegramService(session_dir, workdir)
    cfg_svc = ConfigService()

    with (
        patch("backend.services.config.get_config_service", return_value=cfg_svc),
        patch(
            "backend.utils.proxy.probe_proxy_exit", new_callable=AsyncMock
        ) as mock_probe,
        patch("backend.services.telegram.accounts.logger.warning") as mock_warn,
    ):
        mock_probe.return_value = (ProxyProbeStatus.UNAVAILABLE, "Connection timed out")
        # 不抛异常，平稳放行
        await tg_svc.verify_account_proxy("warn_acc", proxy_dict)
        mock_warn.assert_called()


@pytest.mark.asyncio
async def test_probe_proxy_cached_within_ttl():
    """验证相同代理探测结果在 600s TTL 内直接复用缓存，不重复发网络请求。

    缓存命中时 latency_ms 为 None（绝不把 0ms 当实测延迟），cached 为 True。
    """
    proxy_dict = {"scheme": "socks5", "hostname": "10.0.0.1", "port": 1080}

    with (
        patch(
            "backend.utils.proxy._fetch_ip_via_proxy_detailed",
            new_callable=AsyncMock,
        ) as mock_proxy_ip,
        patch(
            "backend.utils.proxy.get_host_direct_ip", new_callable=AsyncMock
        ) as mock_host_ip,
        patch(
            "backend.utils.proxy.probe_telegram_handshake",
            new=_handshake_ok(),
        ),
    ):
        mock_host_ip.return_value = "1.1.1.1"
        mock_proxy_ip.return_value = ("2.2.2.2", None)

        outcome1 = await probe_proxy_exit(proxy_dict)
        assert outcome1.status == ProxyProbeStatus.OK
        assert outcome1.detail == "2.2.2.2"
        assert outcome1.cached is False
        assert outcome1.latency_ms is not None and outcome1.latency_ms >= 0
        # 两端点并行，各调一次
        assert mock_proxy_ip.call_count == len(DEFAULT_PROBE_ENDPOINTS)

        # 第二次探测相同代理：命中缓存
        outcome2 = await probe_proxy_exit(proxy_dict)
        assert outcome2.status == ProxyProbeStatus.OK
        assert outcome2.detail == "2.2.2.2"
        assert outcome2.cached is True
        assert outcome2.latency_ms is None
        assert mock_proxy_ip.call_count == len(DEFAULT_PROBE_ENDPOINTS)  # 未重复调用


@pytest.mark.asyncio
async def test_probe_proxy_unavailable_is_cached():
    """代理不可达的探测结果同样入缓存，避免每次账号操作都重复阻塞探测。"""
    proxy_dict = {"scheme": "socks5", "hostname": "10.0.0.2", "port": 1080}

    with (
        patch(
            "backend.utils.proxy._fetch_ip_via_proxy_detailed",
            new_callable=AsyncMock,
        ) as mock_proxy_ip,
        patch(
            "backend.utils.proxy.get_host_direct_ip", new_callable=AsyncMock
        ) as mock_host_ip,
        patch(
            "backend.utils.proxy.probe_telegram_handshake",
            new=_handshake_ok(),
        ),
    ):
        mock_host_ip.return_value = "1.1.1.1"
        mock_proxy_ip.return_value = (None, "ConnectTimeout: timed out")

        status1, _ = await probe_proxy_exit(proxy_dict)
        assert status1 == ProxyProbeStatus.UNAVAILABLE
        assert mock_proxy_ip.call_count == len(DEFAULT_PROBE_ENDPOINTS)

        status2, _ = await probe_proxy_exit(proxy_dict)
        assert status2 == ProxyProbeStatus.UNAVAILABLE
        # 命中缓存：不再重复探测
        assert mock_proxy_ip.call_count == len(DEFAULT_PROBE_ENDPOINTS)


@pytest.mark.asyncio
async def test_probe_force_bypasses_cache_with_real_latency():
    """force=True 跳过缓存读，连续两次都走真实网络，延迟为实测值非缓存值。"""
    proxy_dict = {"scheme": "socks5", "hostname": "10.0.0.3", "port": 1080}

    with (
        patch(
            "backend.utils.proxy._fetch_ip_via_proxy_detailed",
            new_callable=AsyncMock,
        ) as mock_proxy_ip,
        patch(
            "backend.utils.proxy.get_host_direct_ip", new_callable=AsyncMock
        ) as mock_host_ip,
        patch(
            "backend.utils.proxy.probe_telegram_handshake",
            new=_handshake_ok(),
        ),
    ):
        mock_host_ip.return_value = "1.1.1.1"
        mock_proxy_ip.return_value = ("2.2.2.2", None)

        first = await probe_proxy_exit(proxy_dict)
        assert first.status == ProxyProbeStatus.OK
        assert first.cached is False

        second = await probe_proxy_exit(proxy_dict, force=True)
        assert second.status == ProxyProbeStatus.OK
        assert second.cached is False
        assert second.latency_ms is not None and second.latency_ms >= 0
        # 两次真实探测 × 两端点
        assert mock_proxy_ip.call_count == 2 * len(DEFAULT_PROBE_ENDPOINTS)

        # 不带 force 时命中缓存
        third = await probe_proxy_exit(proxy_dict)
        assert third.cached is True
        assert third.latency_ms is None
        assert mock_proxy_ip.call_count == 2 * len(DEFAULT_PROBE_ENDPOINTS)


@pytest.mark.asyncio
async def test_probe_http_plaintext_fallback():
    """http 代理 https 端点 CONNECT 全灭、明文 http 端点成功时判 OK。"""
    proxy_dict = {"scheme": "http", "hostname": "10.0.0.4", "port": 8080}

    async def fake_fetch(proxy_dict_arg, endpoint, timeout=5.0):
        if endpoint.startswith("https://"):
            return None, "ProxyError: CONNECT refused"
        return "203.0.113.9", None

    with (
        patch(
            "backend.utils.proxy._fetch_ip_via_proxy_detailed",
            new_callable=AsyncMock,
        ) as mock_proxy_ip,
        patch(
            "backend.utils.proxy.get_host_direct_ip", new_callable=AsyncMock
        ) as mock_host_ip,
        patch(
            "backend.utils.proxy.probe_telegram_handshake",
            new=_handshake_ok(),
        ),
    ):
        mock_host_ip.return_value = "1.1.1.1"
        mock_proxy_ip.side_effect = fake_fetch

        outcome = await probe_proxy_exit(proxy_dict)
        assert outcome.status == ProxyProbeStatus.OK
        assert outcome.detail == "203.0.113.9"
        # https 2 次 + 明文 2 次
        assert mock_proxy_ip.call_count == 4


@pytest.mark.asyncio
async def test_probe_failure_detail_carries_real_error():
    """失败 detail 带真实错误摘要，而非恒定的 Connection timed out。"""
    proxy_dict = {"scheme": "socks5", "hostname": "10.0.0.5", "port": 1080}

    with (
        patch(
            "backend.utils.proxy._fetch_ip_via_proxy_detailed",
            new_callable=AsyncMock,
        ) as mock_proxy_ip,
        patch(
            "backend.utils.proxy.get_host_direct_ip", new_callable=AsyncMock
        ) as mock_host_ip,
        patch(
            "backend.utils.proxy.probe_telegram_handshake",
            new=_handshake_ok(),
        ),
    ):
        mock_host_ip.return_value = "1.1.1.1"
        mock_proxy_ip.return_value = (
            None,
            "ProxyError: 407 Proxy Authentication Required",
        )

        outcome = await probe_proxy_exit(proxy_dict)
        assert outcome.status == ProxyProbeStatus.UNAVAILABLE
        assert "407" in (outcome.detail or "")
        assert outcome.detail != "Connection timed out"


@pytest.mark.asyncio
async def test_probe_handshake_failure_short_circuits():
    """握手快筛失败直接返回细粒度错误码，不再烧端点超时。"""
    proxy_dict = {"scheme": "socks5", "hostname": "10.0.0.6", "port": 1080}

    with (
        patch(
            "backend.utils.proxy._fetch_ip_via_proxy_detailed",
            new_callable=AsyncMock,
        ) as mock_proxy_ip,
        patch(
            "backend.utils.proxy.get_host_direct_ip", new_callable=AsyncMock
        ) as mock_host_ip,
        patch(
            "backend.utils.proxy.probe_telegram_handshake",
            new_callable=AsyncMock,
        ) as mock_handshake,
    ):
        mock_host_ip.return_value = "1.1.1.1"
        mock_handshake.return_value = HandshakeResult(
            ok=False,
            code="PROXY_AUTH_FAILED",
            message="代理认证失败：用户名或密码错误",
            latency_ms=12,
        )

        outcome = await probe_proxy_exit(proxy_dict)
        assert outcome.status == ProxyProbeStatus.UNAVAILABLE
        assert outcome.code == "PROXY_AUTH_FAILED"
        assert "PROXY_AUTH_FAILED" in (outcome.detail or "")
        assert outcome.latency_ms == 12
        assert mock_proxy_ip.call_count == 0


@pytest.mark.asyncio
async def test_probe_manual_failure_not_cached_and_no_breaker_count():
    """手动测试失败不写缓存、不累计熔断；连续点测每次都真实探测。"""
    proxy_dict = {"scheme": "socks5", "hostname": "10.0.0.7", "port": 1080}
    breaker = get_proxy_circuit_breaker()

    with (
        patch(
            "backend.utils.proxy._fetch_ip_via_proxy_detailed",
            new_callable=AsyncMock,
        ) as mock_proxy_ip,
        patch(
            "backend.utils.proxy.get_host_direct_ip", new_callable=AsyncMock
        ) as mock_host_ip,
        patch(
            "backend.utils.proxy.probe_telegram_handshake",
            new=_handshake_ok(),
        ),
    ):
        mock_host_ip.return_value = "1.1.1.1"
        mock_proxy_ip.return_value = (None, "ConnectTimeout: timed out")

        first = await probe_proxy_exit(proxy_dict, force=True, manual=True)
        assert first.status == ProxyProbeStatus.UNAVAILABLE
        assert first.cached is False
        second = await probe_proxy_exit(proxy_dict, force=True, manual=True)
        assert second.status == ProxyProbeStatus.UNAVAILABLE
        # 两次手动探测都不走缓存：2 次 × 2 端点
        assert mock_proxy_ip.call_count == 2 * len(DEFAULT_PROBE_ENDPOINTS)

    assert breaker.get_state(proxy_dict) == CircuitState.HEALTHY


@pytest.mark.asyncio
async def test_probe_manual_bypasses_tripped_breaker():
    """熔断 TRIPPED 时手动测试仍真实探测，账号门禁仍拦截。"""
    proxy_dict = {"scheme": "socks5", "hostname": "10.0.0.8", "port": 1080}
    breaker = get_proxy_circuit_breaker()
    for _ in range(3):
        breaker.record_failure(proxy_dict)
    assert breaker.get_state(proxy_dict) == CircuitState.TRIPPED

    with (
        patch(
            "backend.utils.proxy._fetch_ip_via_proxy_detailed",
            new_callable=AsyncMock,
        ) as mock_proxy_ip,
        patch(
            "backend.utils.proxy.get_host_direct_ip", new_callable=AsyncMock
        ) as mock_host_ip,
        patch(
            "backend.utils.proxy.probe_telegram_handshake",
            new=_handshake_ok(),
        ),
    ):
        mock_host_ip.return_value = "198.51.100.1"
        mock_proxy_ip.return_value = ("203.0.113.5", None)

        # 门禁路径先行：熔断拦截，不发网络请求
        gated = await probe_proxy_exit(proxy_dict)
        assert gated.status == ProxyProbeStatus.UNAVAILABLE
        assert "circuit breaker" in (gated.detail or "").lower()

        # 手动测试绕过熔断门禁，仍真实探测
        manual = await probe_proxy_exit(proxy_dict, force=True, manual=True)
        assert manual.status == ProxyProbeStatus.OK
        assert manual.detail == "203.0.113.5"
        # 手动成功不复位熔断计数
        assert breaker.get_state(proxy_dict) == CircuitState.TRIPPED


@pytest.mark.asyncio
async def test_check_account_status_reports_invalid_proxy_as_blocked(
    tmp_path, monkeypatch
):
    """代理字符串非法时必须返回 blocked 状态与稳定错误码，而不是静默降级为直连。"""
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True)
    workdir = tmp_path / "data"
    workdir.mkdir(parents=True)

    (session_dir / "bad_proxy_acc.session").write_text("dummy")
    set_account_profile("bad_proxy_acc", proxy="not-a-valid-proxy-url")

    tg_svc = DummyTelegramService(session_dir, workdir)

    with patch(
        "backend.services.config.get_config_service", return_value=ConfigService()
    ):
        result = await tg_svc.check_account_status("bad_proxy_acc", timeout_seconds=1.0)

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["code"] == "PROXY_INVALID_BLOCKED"


def test_format_proxy_url_ipv6_and_user():
    from backend.utils.proxy import format_proxy_url

    # IPv6 host 自动包裹方括号
    p1 = {"scheme": "socks5", "hostname": "::1", "port": 1080}
    assert format_proxy_url(p1) == "socks5://[::1]:1080"

    p2 = {
        "scheme": "http",
        "hostname": "2001:db8::1",
        "port": 8080,
        "username": "alice",
    }
    assert format_proxy_url(p2) == "http://alice@[2001:db8::1]:8080"

    p3 = {
        "scheme": "http",
        "hostname": "2001:db8::1",
        "port": 8080,
        "username": "alice",
        "password": "pwd",
    }
    assert format_proxy_url(p3) == "http://alice:pwd@[2001:db8::1]:8080"

    # 已经带有中括号的保持原样
    p4 = {"scheme": "socks5", "hostname": "[::1]", "port": 1080}
    assert format_proxy_url(p4) == "socks5://[::1]:1080"
