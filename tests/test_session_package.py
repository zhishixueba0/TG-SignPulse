"""Tests for direct session-package export (copy current auth_key to ZIP)."""

from __future__ import annotations

import base64
import io
import json
import sqlite3
import struct
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services.telegram.session_package import (
    _read_auth_from_session_string,
    build_session_zip,
)
from tests.test_api import _auth, _login, api_client, db  # noqa: F401

AUTH_KEY = bytes(range(256))
API_HASH = "ab" * 16  # 32 hex


def _make_pyrogram_session(
    session_dir: Path,
    name: str,
    *,
    dc_id: int = 1,
    api_id: int = 12345,
    test_mode: bool = False,
    auth_key: bytes = AUTH_KEY,
    user_id: int = 8789956089,
) -> Path:
    """Minimal Pyrogram-format .session SQLite."""
    path = session_dir / f"{name}.session"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE sessions "
            "(dc_id INTEGER PRIMARY KEY, server_address TEXT, port INTEGER, "
            "api_id INTEGER, test_mode INTEGER, auth_key BLOB, date INTEGER, "
            "user_id INTEGER, is_bot INTEGER)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dc_id,
                "149.154.175.53",
                443,
                api_id,
                int(test_mode),
                auth_key,
                1700000000,
                user_id,
                0,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _patch_creds_and_profile():
    """Patch credential resolution + profile reads for build_session_zip."""
    return (
        patch(
            "backend.services.telegram.session_package.resolve_telegram_api_credentials",
            return_value=(12345, API_HASH),
        ),
        patch(
            "backend.services.telegram.session_package.get_account_profile",
            return_value={
                "username": "Nora_Hutton",
                "first_name": "Nora",
                "phone": "12098814721",
            },
        ),
    )


def test_build_session_zip_happy_path(tmp_path):
    _make_pyrogram_session(tmp_path, "12098814721")
    creds_patch, profile_patch = _patch_creds_and_profile()
    with creds_patch, profile_patch:
        zip_bytes, filename = build_session_zip(
            "12098814721", session_dir=tmp_path, exported_by="admin"
        )

    assert filename == "Nora_Hutton.zip"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert names == ["Nora_Hutton.session", "Nora_Hutton.json"]
        session_bytes = zf.read("Nora_Hutton.session")
        meta = json.loads(zf.read("Nora_Hutton.json"))

    # Telethon-format SQLite: sessions row carries the same auth_key
    tmp_session = tmp_path / "check.session"
    tmp_session.write_bytes(session_bytes)
    conn = sqlite3.connect(str(tmp_session))
    try:
        row = conn.execute(
            "SELECT dc_id, server_address, port, auth_key FROM sessions"
        ).fetchone()
        version = conn.execute("SELECT version FROM version").fetchone()
    finally:
        conn.close()
    assert version == (7,)
    assert row[0] == 1
    assert row[1] == "149.154.175.53"
    assert row[2] == 443
    assert len(row[3]) == 256
    assert bytes(row[3]) == AUTH_KEY

    assert meta["id"] == 8789956089
    assert meta["app_id"] == 12345
    assert meta["app_hash"] == API_HASH
    assert len(meta["app_hash"]) == 32
    assert all(c in "0123456789abcdef" for c in meta["app_hash"])
    assert meta["username"] == "Nora_Hutton"
    assert meta["phone"] == "12098814721"
    assert meta["exported_by"] == "admin"
    assert meta["exported_at"]
    assert "do not use it in two places" in meta["note"]


def test_build_session_zip_missing_account(tmp_path):
    with pytest.raises(ValueError, match="ACCOUNT_NOT_FOUND"):
        build_session_zip("nope", session_dir=tmp_path)


def test_build_session_zip_short_auth_key(tmp_path):
    _make_pyrogram_session(tmp_path, "badkey", auth_key=b"short")
    creds_patch, profile_patch = _patch_creds_and_profile()
    with (
        creds_patch,
        profile_patch,
        pytest.raises(ValueError, match="SESSION_NOT_EXPORTABLE"),
    ):
        build_session_zip("badkey", session_dir=tmp_path)


def test_build_session_zip_test_mode_rejected(tmp_path):
    _make_pyrogram_session(tmp_path, "testmode", test_mode=True)
    creds_patch, profile_patch = _patch_creds_and_profile()
    with (
        creds_patch,
        profile_patch,
        pytest.raises(ValueError, match="SESSION_NOT_EXPORTABLE"),
    ):
        build_session_zip("testmode", session_dir=tmp_path)


def test_build_session_zip_base_falls_back_to_account_name(tmp_path):
    _make_pyrogram_session(tmp_path, "12098814721")
    creds_patch, _ = _patch_creds_and_profile()
    with (
        creds_patch,
        patch(
            "backend.services.telegram.session_package.get_account_profile",
            return_value={},
        ),
    ):
        zip_bytes, filename = build_session_zip("12098814721", session_dir=tmp_path)
    assert filename == "12098814721.zip"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert zf.namelist() == ["12098814721.session", "12098814721.json"]


def test_read_auth_from_session_string_fallback(tmp_path):
    # .session without a sessions table: fall back to the stored string.
    session_string = base64.urlsafe_b64encode(
        struct.pack(">BI?256sQ?", 2, 999, False, AUTH_KEY, 4242, False)
    ).decode("ascii")
    # The helper imports _export_session_string_from_file from tg_session
    # at call time, so patch it at its definition site.
    with patch(
        "backend.utils.tg_session._export_session_string_from_file",
        return_value=session_string,
    ):
        auth = _read_auth_from_session_string(tmp_path, "fallback_acc")
    assert auth is not None
    assert auth["dc_id"] == 2
    assert auth["api_id"] == 999
    assert auth["user_id"] == 4242
    assert bytes(auth["auth_key"]) == AUTH_KEY


def test_export_session_route_password_and_rate_limit(api_client, db):  # noqa: F811
    token = _login(api_client)
    fake_zip = b"PK\x03\x04fake-zip-bytes"

    with patch(
        "backend.api.routes.account_tools.build_session_zip",
        return_value=(fake_zip, "Nora_Hutton.zip"),
    ):
        # Wrong panel password -> 403 PANEL_PASSWORD_WRONG
        resp = api_client.post(
            "/api/account-tools/acc1/export-session",
            json={"password": "wrong"},
            headers=_auth(token),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "PANEL_PASSWORD_WRONG"

        # Correct password -> 200 ZIP download
        resp = api_client.post(
            "/api/account-tools/acc1/export-session",
            json={"password": "admin123"},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/zip")
        assert "Nora_Hutton.zip" in resp.headers["content-disposition"]
        assert resp.headers["cache-control"] == "no-store"
        assert resp.content == fake_zip

        # 5 attempts / 10 min: 3 more OK, then the 6th is rate-limited
        for _ in range(3):
            resp = api_client.post(
                "/api/account-tools/acc1/export-session",
                json={"password": "admin123"},
                headers=_auth(token),
            )
            assert resp.status_code == 200, resp.text
        resp = api_client.post(
            "/api/account-tools/acc1/export-session",
            json={"password": "admin123"},
            headers=_auth(token),
        )
        assert resp.status_code == 429, resp.text


def test_export_session_route_missing_account(api_client, db):  # noqa: F811
    token = _login(api_client)

    with patch(
        "backend.api.routes.account_tools.build_session_zip",
        side_effect=ValueError("ACCOUNT_NOT_FOUND"),
    ):
        resp = api_client.post(
            "/api/account-tools/ghost/export-session",
            json={"password": "admin123"},
            headers=_auth(token),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "ACCOUNT_NOT_FOUND"
