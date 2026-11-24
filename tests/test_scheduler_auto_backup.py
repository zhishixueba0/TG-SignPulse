"""自动备份调度任务：WebDAV 上传结果透传与失败通知。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

WEBDAV_CFG = {
    "auto_backup_enabled": True,
    "auto_backup_interval_hours": 24,
    "auto_backup_keep": 2,
    "webdav_url": "https://93.184.216.34/dav",
    "webdav_username": "u",
    "webdav_password": "p",
}


def _drive(result: dict) -> object:
    """以给定 run_auto_backup 返回值驱动一次调度任务，返回其 mock。"""
    from backend.scheduler import _job_auto_backup

    with patch(
        "backend.services.backup_archive.run_auto_backup", return_value=result
    ) as run_m:
        asyncio.run(_job_auto_backup())
    return run_m


class TestAutoBackupJob:
    def test_passes_webdav_settings_and_notifies_on_failure(self, isolated_env: Path):
        from backend.services.config import get_config_service

        get_config_service().save_global_settings(dict(WEBDAV_CFG))

        with patch(
            "backend.services.push_notifications.send_auto_backup_failure_notification",
            new_callable=AsyncMock,
        ) as notify_m:
            run_m = _drive(
                {
                    "success": True,
                    "path": "https://93.184.216.34/dav/auto-1.tar.gz",
                    "size_bytes": 10,
                    "pruned": 0,
                    "remote_pruned": 1,
                    "local_removed": True,
                    "webdav": {"success": False, "error": "HTTP 502"},
                }
            )

        kwargs = run_m.call_args.kwargs
        assert kwargs["webdav_settings"]["webdav_url"] == "https://93.184.216.34/dav"
        notify_m.assert_called_once()
        assert "HTTP 502" in notify_m.call_args.kwargs["error"]

    def test_no_notification_when_webdav_upload_succeeds(self, isolated_env: Path):
        from backend.services.config import get_config_service

        get_config_service().save_global_settings(dict(WEBDAV_CFG))

        with patch(
            "backend.services.push_notifications.send_auto_backup_failure_notification",
            new_callable=AsyncMock,
        ) as notify_m:
            _drive(
                {
                    "success": True,
                    "path": "https://93.184.216.34/dav/auto-1.tar.gz",
                    "size_bytes": 10,
                    "pruned": 0,
                    "remote_pruned": 0,
                    "local_removed": True,
                    "webdav": {"success": True},
                }
            )
        notify_m.assert_not_called()

    def test_disabled_auto_backup_skips(self, isolated_env: Path):
        from backend.scheduler import _job_auto_backup
        from backend.services.config import get_config_service

        get_config_service().save_global_settings({"auto_backup_enabled": False})
        with patch("backend.services.backup_archive.run_auto_backup") as run_m:
            asyncio.run(_job_auto_backup())
        run_m.assert_not_called()


class TestAutoBackupFormat:
    def test_spbak_format_uses_custom_password(self, isolated_env: Path):
        from backend.services.config import get_config_service

        get_config_service().save_global_settings(
            dict(
                WEBDAV_CFG,
                auto_backup_format="spbak",
                auto_backup_password="my-pw",
            )
        )
        run_m = _drive({"success": True, "webdav": {"success": True}})
        kwargs = run_m.call_args.kwargs
        assert kwargs["encrypt"] is True
        assert kwargs["password"] == "my-pw"

    def test_tar_gz_format_is_unencrypted(self, isolated_env: Path):
        from backend.services.config import get_config_service

        get_config_service().save_global_settings(
            dict(WEBDAV_CFG, auto_backup_format="tar.gz")
        )
        run_m = _drive({"success": True, "webdav": {"success": True}})
        kwargs = run_m.call_args.kwargs
        assert kwargs["encrypt"] is False
        assert kwargs["password"] is None

    def test_auto_format_uses_secret_key(self, isolated_env: Path):
        from backend.services.config import get_config_service

        get_config_service().save_global_settings(
            dict(WEBDAV_CFG, auto_backup_format="auto")
        )
        run_m = _drive({"success": True, "webdav": {"success": True}})
        kwargs = run_m.call_args.kwargs
        assert kwargs["encrypt"] is True
        assert kwargs["password"] is None


class _FakeJob:
    """只保留 _sync_auto_backup_job 读到的 trigger.interval。"""

    def __init__(self, interval: timedelta):
        from types import SimpleNamespace

        self.trigger = SimpleNamespace(interval=interval)


class _FakeScheduler:
    """记录 add/remove 调用；未启动的真调度器上 replace_existing 不生效，
    无法用来断言“是否重新注册”，所以用假的锁住这层逻辑。"""

    def __init__(self) -> None:
        self.jobs: dict = {}
        self.add_calls = 0
        self.remove_calls = 0

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def add_job(self, func, trigger, id, replace_existing=False, **kwargs):
        self.jobs[id] = _FakeJob(trigger.interval)
        self.add_calls += 1

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)
        self.remove_calls += 1


class TestSyncAutoBackupJob:
    """重复同步不重置倒计时；间隔改了才重新注册。"""

    @staticmethod
    def _setup_scheduler(monkeypatch):
        import backend.scheduler as sched_mod

        sched = _FakeScheduler()
        monkeypatch.setattr(sched_mod, "scheduler", sched)
        return sched

    def test_repeated_sync_does_not_readd(self, isolated_env: Path, monkeypatch):
        from backend.scheduler import _sync_auto_backup_job
        from backend.services.config import get_config_service

        get_config_service().save_global_settings(dict(WEBDAV_CFG))
        sched = self._setup_scheduler(monkeypatch)

        _sync_auto_backup_job()
        assert sched.add_calls == 1
        assert sched.get_job("system-auto-backup") is not None

        # 间隔没变：再次同步不应重新注册，否则倒计时被重置
        _sync_auto_backup_job()
        assert sched.add_calls == 1

    def test_interval_change_replaces_job(self, isolated_env: Path, monkeypatch):
        from backend.scheduler import _sync_auto_backup_job
        from backend.services.config import get_config_service

        get_config_service().save_global_settings(dict(WEBDAV_CFG))
        sched = self._setup_scheduler(monkeypatch)

        _sync_auto_backup_job()
        job = sched.get_job("system-auto-backup")
        assert job is not None
        assert job.trigger.interval == timedelta(hours=24)

        get_config_service().save_global_settings(
            dict(WEBDAV_CFG, auto_backup_interval_hours=12)
        )
        _sync_auto_backup_job()
        assert sched.add_calls == 2
        job = sched.get_job("system-auto-backup")
        assert job is not None
        assert job.trigger.interval == timedelta(hours=12)

    def test_disabled_auto_backup_removes_job(self, isolated_env: Path, monkeypatch):
        from backend.scheduler import _sync_auto_backup_job
        from backend.services.config import get_config_service

        get_config_service().save_global_settings(dict(WEBDAV_CFG))
        sched = self._setup_scheduler(monkeypatch)

        _sync_auto_backup_job()
        assert sched.get_job("system-auto-backup") is not None

        get_config_service().save_global_settings({"auto_backup_enabled": False})
        _sync_auto_backup_job()
        assert sched.get_job("system-auto-backup") is None
        assert sched.remove_calls == 1
