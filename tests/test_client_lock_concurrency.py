import asyncio
import gc
import weakref
from unittest.mock import MagicMock

import pytest

from tg_signer.core.client import (
    _CLIENT_ASYNC_LOCKS,
    _CLIENT_INSTANCES,
    _CLIENT_REFS,
    get_client_lock,
    invalidate_cached_client,
)


@pytest.mark.asyncio
async def test_client_lock_same_loop_identical():
    """Same key on same loop returns identical lock."""
    lock1 = get_client_lock("acc_same_loop")
    lock2 = get_client_lock("acc_same_loop")
    assert lock1 is lock2
    assert isinstance(lock1, asyncio.Lock)


def test_client_lock_distinct_loops_separate():
    """Same key on distinct loops returns separate locks."""
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    try:
        lock_a = get_client_lock("acc_distinct", loop=loop_a)
        lock_b = get_client_lock("acc_distinct", loop=loop_b)
        assert lock_a is not lock_b
    finally:
        loop_a.close()
        loop_b.close()


@pytest.mark.asyncio
async def test_client_lock_weakref_and_isolation():
    """Garbage collecting a closed event loop automatically cleans up its lock registry without memory leaks."""

    def _create_and_destroy_loop():
        loop = asyncio.new_event_loop()
        ref = weakref.ref(loop)
        lock = get_client_lock("acc_test", loop=loop)
        assert isinstance(lock, asyncio.Lock)
        # Ensure it exists in the weak dictionary before collection
        assert loop in _CLIENT_ASYNC_LOCKS
        loop.close()
        del loop
        gc.collect()
        return ref() is None

    assert await asyncio.to_thread(_create_and_destroy_loop) is True


@pytest.mark.asyncio
async def test_invalidate_cached_client():
    """invalidate_cached_client(key) clears cached client, refcounts, and lock safely."""
    key = "acc_to_invalidate"
    mem_key = f"{key}::memory"

    lock1 = get_client_lock(key)
    lock_mem1 = get_client_lock(mem_key)

    mock_client = MagicMock()
    mock_mem_client = MagicMock()

    _CLIENT_INSTANCES[key] = mock_client
    _CLIENT_INSTANCES[mem_key] = mock_mem_client
    _CLIENT_REFS[key] = 2
    _CLIENT_REFS[mem_key] = 1

    invalidate_cached_client(key)

    assert key not in _CLIENT_INSTANCES
    assert mem_key not in _CLIENT_INSTANCES
    assert key not in _CLIENT_REFS
    assert mem_key not in _CLIENT_REFS

    # Requesting lock again must return a fresh lock instance
    lock2 = get_client_lock(key)
    lock_mem2 = get_client_lock(mem_key)

    assert lock2 is not lock1
    assert lock_mem2 is not lock_mem1


@pytest.mark.asyncio
async def test_client_start_database_locked_fails_fast(tmp_path):
    """客户端启动撞上 session 写锁时快速失败：最多尝试 2 次就报错。

    回归 2026-11-20 卡死：旧值 5 次重试、等待 2/5/8/11 秒，撞锁后占着
    账号锁重试近 40 秒，拖垮线程池。快速失败只留一次重试给瞬态锁。
    """
    from unittest.mock import AsyncMock, patch

    from tg_signer.core.client import Client

    client = Client(
        "test_failfast",
        api_id=12345,
        api_hash="abc123",
        workdir=str(tmp_path),
        in_memory=False,
    )
    calls = {"n": 0}

    async def fake_connect():
        calls["n"] += 1
        raise Exception("database is locked")

    client.connect = fake_connect
    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(Exception, match="database is locked"):
            await client.__aenter__()
    assert calls["n"] == 2
    assert _CLIENT_REFS.get(client.key, 0) == 0
    assert client.key not in _CLIENT_INSTANCES
