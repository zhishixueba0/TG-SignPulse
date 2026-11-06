"""卡片手机号：从有效会话的 peers 表直接读本人手机号（离线，不连 Telegram）。

回归：账号 12098814721 的 profile 缓存里 phone 为 None，卡片显示“未知”，
但 session 文件的 peers 表里早就有本人（id=user_id，type='user'）的
phone_number。_account_entry 应优先用 profile 缓存，缺失时用 session 里
的缓存号码兜底。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.utils.tg_session import get_account_session_identity


def _make_session(
    session_dir: Path, name: str, user_id: int, dc_id: int = 1, phone=None
) -> Path:
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{name}.session"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE sessions (dc_id INTEGER, server_address TEXT, port INTEGER,"
        " api_id INTEGER, test_mode INTEGER, auth_key BLOB, date INTEGER,"
        " user_id INTEGER, is_bot INTEGER)"
    )
    con.execute(
        "INSERT INTO sessions VALUES (?, '149.154.175.60', 443, 12345, 0,"
        " x'00', 0, ?, 0)",
        (dc_id, user_id),
    )
    if phone is not False:
        con.execute(
            "CREATE TABLE peers (id INTEGER, access_hash INTEGER, type TEXT,"
            " phone_number TEXT, last_update_on INTEGER)"
        )
        if phone is not None:
            con.execute(
                "INSERT INTO peers VALUES (?, 0, 'user', ?, 0)",
                (user_id, phone),
            )
    con.commit()
    con.close()
    return path


def test_identity_reads_phone_from_peers(isolated_env: Path):
    session_dir = isolated_env / "sessions"
    _make_session(session_dir, "12098814721", 8789956089, phone="12098814721")
    ident = get_account_session_identity("12098814721")
    assert ident is not None
    assert ident["dc_id"] == 1
    assert ident["user_id"] == 8789956089
    assert ident["phone"] == "12098814721"


def test_identity_phone_none_when_no_peers_table(isolated_env: Path):
    session_dir = isolated_env / "sessions"
    _make_session(session_dir, "nopeers", 12345, phone=False)
    ident = get_account_session_identity("nopeers")
    assert ident is not None
    assert ident["user_id"] == 12345
    assert ident["phone"] is None


def test_identity_phone_none_when_own_peer_missing(isolated_env: Path):
    session_dir = isolated_env / "sessions"
    # peers 表存在但没有本人这一行（只有 777000）
    _make_session(session_dir, "other", 12345, phone=None)
    con = sqlite3.connect(session_dir / "other.session")
    con.execute("INSERT INTO peers VALUES (777000, 0, 'user', '42777', 0)")
    con.commit()
    con.close()
    ident = get_account_session_identity("other")
    assert ident is not None
    assert ident["phone"] is None


def test_identity_none_when_no_session_file(isolated_env: Path):
    assert get_account_session_identity("ghost") is None
