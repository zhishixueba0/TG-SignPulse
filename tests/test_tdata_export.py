"""Tests for TData export (Round 4 item 3).

build_tdata_zip converts the account's CURRENT auth_key (same material
as build_session_zip) into a Telegram Desktop tdata folder via opentele,
offline, and zips it with a top-level ``tdata/`` folder. The round-trip
test loads the produced ZIP back with opentele and checks the user id.
"""

from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.telegram.tdata_exporter import (
    build_tdata_zip,
    verify_tdata_zip,
)
from tests.test_api import _auth, _login, api_client, db  # noqa: F401
from tests.test_session_package import _make_pyrogram_session

API_HASH = "ab" * 16  # 32 hex


def _patch_creds_and_profile():
    return (
        patch(
            "backend.services.telegram.tdata_exporter.resolve_telegram_api_credentials",
            return_value=(12345, API_HASH),
        ),
        patch(
            "backend.services.telegram.tdata_exporter.get_account_profile",
            return_value={
                "username": "Nora_Hutton",
                "first_name": "Nora",
                "phone": "12098814721",
            },
        ),
        patch(
            "backend.services.telegram.tdata_exporter.get_config_service",
            return_value=SimpleNamespace(get_telegram_config=lambda: {}),
        ),
    )


@pytest.mark.asyncio
async def test_build_tdata_zip_round_trip(tmp_path):
    pytest.importorskip("opentele")
    _make_pyrogram_session(tmp_path, "12098814721")
    creds_patch, profile_patch, cfg_patch = _patch_creds_and_profile()
    with creds_patch, profile_patch, cfg_patch:
        zip_bytes, filename = await build_tdata_zip("12098814721", session_dir=tmp_path)
    assert filename == "Nora_Hutton-tdata.zip"
    info = verify_tdata_zip(zip_bytes)
    assert "tdata/key_datas" in info["files"]
    assert any(n.startswith("tdata/") and n.endswith("/maps") for n in info["files"])

    # Round-trip: load the produced tdata back with opentele.
    from opentele.td import TDesktop

    extract_dir = tmp_path / "extracted"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(extract_dir)
    tdesk = TDesktop(basePath=str(extract_dir / "tdata"))
    assert tdesk.isLoaded()
    assert len(tdesk.accounts) == 1
    assert tdesk.accounts[0].UserId == 8789956089


@pytest.mark.asyncio
async def test_build_tdata_zip_missing_account(tmp_path):
    pytest.importorskip("opentele")
    creds_patch, profile_patch, cfg_patch = _patch_creds_and_profile()
    with creds_patch, profile_patch, cfg_patch:
        with pytest.raises(ValueError, match="ACCOUNT_NOT_FOUND"):
            await build_tdata_zip("ghost", session_dir=tmp_path)


def test_verify_tdata_zip_rejects_bad_shape():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("12098814721.session", b"x")
    with pytest.raises(ValueError, match="TDATA_INVALID_STRUCTURE"):
        verify_tdata_zip(buf.getvalue())


def test_export_tdata_route_format_param(api_client, db):  # noqa: F811
    token = _login(api_client)
    fake_zip = b"PK\x03\x04fake-tdata-zip"
    with patch(
        "backend.api.routes.account_tools.build_tdata_zip",
        new=AsyncMock(return_value=(fake_zip, "Nora_Hutton-tdata.zip")),
    ):
        resp = api_client.post(
            "/api/account-tools/acc9/export-session",
            json={"password": "admin123", "format": "tdata"},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/zip")
        assert "Nora_Hutton-tdata.zip" in resp.headers["content-disposition"]
        assert resp.content == fake_zip

        # Invalid format -> 400 EXPORT_FORMAT_INVALID (before password check).
        resp = api_client.post(
            "/api/account-tools/acc9/export-session",
            json={"password": "admin123", "format": "bogus"},
            headers=_auth(token),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "EXPORT_FORMAT_INVALID"


def test_export_tdata_route_converter_unavailable(api_client, db):  # noqa: F811
    token = _login(api_client)
    with patch(
        "backend.api.routes.account_tools.build_tdata_zip",
        new=AsyncMock(
            side_effect=ValueError(
                "TDATA_CONVERTER_UNAVAILABLE: Optional dependency 'opentele' "
                "is not installed"
            )
        ),
    ):
        resp = api_client.post(
            "/api/account-tools/acc9/export-session",
            json={"password": "admin123", "format": "tdata"},
            headers=_auth(token),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"].startswith("TDATA_CONVERTER_UNAVAILABLE")
