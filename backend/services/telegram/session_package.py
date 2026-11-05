"""Direct session-package export (Drive 1.0.129 ``build_session_zip``, ported).

Copies the account's CURRENT auth_key verbatim into a fresh Telethon-format
SQLite session plus JSON metadata, zipped as ``{base}.session`` +
``{base}.json``. Unlike the derived export in ``session_exporter.py``
(``auth.ExportLoginToken`` / ``auth.AcceptLoginToken``, which mints a brand
new device), this does NOT create a new authorization: the exported file is
the same login, so it must never run in two places at once (see the ``note``
field in the JSON metadata).

Auth material is read read-only: first the ``.session`` SQLite
(``?mode=ro``), falling back to the stored session string. The auth_key
must be exactly 256 bytes and ``test_mode`` must be false, otherwise the
account is ``SESSION_NOT_EXPORTABLE``. No network call is made; the only
cost is a local SQLite read plus zip packing.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import sqlite3
import struct
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

from backend.core.config import get_settings
from backend.services.config import get_config_service
from backend.services.telegram.credentials import resolve_telegram_api_credentials
from backend.services.telegram.session_importer import get_default_dc_endpoint
from backend.utils.names import validate_storage_name
from backend.utils.tg_session import get_account_profile
from backend.utils.time import utc_now_iso_z

logger = logging.getLogger("backend.telegram.session_package")

# Telethon session SQLite, version 7 layout (same tables the fork importer
# reads back in convert_telethon_to_pyrogram_sqlite).
_TELETHON_SCHEMA = """
CREATE TABLE version (version INTEGER PRIMARY KEY);
CREATE TABLE sessions (
    dc_id INTEGER PRIMARY KEY,
    server_address TEXT,
    port INTEGER,
    auth_key BLOB,
    takeout_id INTEGER
);
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    hash INTEGER NOT NULL,
    username TEXT,
    phone TEXT,
    name TEXT,
    date INTEGER
);
CREATE TABLE sent_files (
    md5_digest BLOB,
    type INTEGER,
    id INTEGER,
    hash INTEGER,
    PRIMARY KEY (md5_digest, type)
);
CREATE TABLE update_state (
    id INTEGER PRIMARY KEY,
    pts INTEGER,
    qts INTEGER,
    date INTEGER,
    seq INTEGER
);
"""

_EXPORT_NOTE = (
    "This file is a full login of the account. Keep it private; "
    "do not use it in two places at once."
)

_SESSION_STRING_FORMAT = ">BI?256sQ?"


def _clean_base_name(username: Optional[str], account_name: str, user_id: int) -> str:
    """ZIP entry base: cleaned Telegram username, else account name, else id."""
    candidate = (username or "").strip() or account_name.strip() or str(user_id)
    cleaned = re.sub(r"[^\w.-]+", "_", candidate).strip("._")
    return cleaned or str(user_id)


def _read_auth_from_sqlite(session_file: Path) -> Optional[dict[str, Any]]:
    """Read dc/api_id/test_mode/auth_key/user_id from a .session file (ro)."""
    if not session_file.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{session_file}?mode=ro", uri=True, timeout=5)
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT dc_id, api_id, test_mode, auth_key, user_id, is_bot FROM sessions"
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if not row:
        return None
    dc_id, api_id, test_mode, auth_key, user_id, is_bot = row
    if isinstance(auth_key, memoryview):
        auth_key = auth_key.tobytes()
    return {
        "dc_id": dc_id,
        "api_id": api_id,
        "test_mode": bool(test_mode),
        "auth_key": bytes(auth_key) if auth_key is not None else None,
        "user_id": user_id,
        "is_bot": bool(is_bot),
    }


def _read_auth_from_session_string(
    session_dir: Path, account_name: str
) -> Optional[dict[str, Any]]:
    """Fallback: decode the stored Pyrogram session string (271-byte form)."""
    from backend.utils.tg_session import _export_session_string_from_file

    session_string = _export_session_string_from_file(session_dir, account_name)
    if not session_string:
        return None
    try:
        raw = base64.urlsafe_b64decode(
            session_string + "=" * (-len(session_string) % 4)
        )
        if len(raw) != struct.calcsize(_SESSION_STRING_FORMAT):
            return None
        dc_id, api_id, test_mode, auth_key, user_id, is_bot = struct.unpack(
            _SESSION_STRING_FORMAT, raw
        )
    except Exception:
        return None
    return {
        "dc_id": dc_id,
        "api_id": api_id,
        "test_mode": bool(test_mode),
        "auth_key": bytes(auth_key),
        "user_id": user_id,
        "is_bot": bool(is_bot),
    }


def _read_auth(account_name: str, session_dir: Path) -> dict[str, Any]:
    """Read and validate the account's auth material, or raise ValueError."""
    session_file = session_dir / f"{account_name}.session"
    auth = _read_auth_from_sqlite(session_file)
    if auth is None:
        auth = _read_auth_from_session_string(session_dir, account_name)
    if auth is None:
        raise ValueError("SESSION_NOT_EXPORTABLE: 无法读取账号会话")
    auth_key = auth.get("auth_key")
    if not isinstance(auth_key, (bytes, bytearray)) or len(auth_key) != 256:
        raise ValueError("SESSION_NOT_EXPORTABLE: auth_key 不是 256 字节")
    if auth.get("test_mode"):
        raise ValueError("SESSION_NOT_EXPORTABLE: 测试模式会话不可导出")
    user_id = auth.get("user_id")
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("SESSION_NOT_EXPORTABLE: user_id 无效")
    dc_id = auth.get("dc_id")
    try:
        get_default_dc_endpoint(int(dc_id), False)
    except Exception as exc:
        raise ValueError(f"SESSION_NOT_EXPORTABLE: 未知的 DC {dc_id}") from exc
    return auth


def _build_telethon_session_bytes(auth: dict[str, Any]) -> bytes:
    """Build a Telethon-format .session SQLite file in memory."""
    dc_id = int(auth["dc_id"])
    server_address, port = get_default_dc_endpoint(dc_id, False)
    auth_key = bytes(auth["auth_key"])
    with tempfile.TemporaryDirectory(prefix="tg-export-") as tmpdir:
        db_path = Path(tmpdir) / "export.session"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(_TELETHON_SCHEMA)
            conn.execute("INSERT INTO version VALUES (?)", (7,))
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, NULL)",
                (dc_id, server_address, port, auth_key),
            )
            conn.commit()
        finally:
            conn.close()
        return db_path.read_bytes()


def build_session_zip(
    account_name: str,
    *,
    session_dir: Optional[Path] = None,
    exported_by: Optional[str] = None,
) -> tuple[bytes, str]:
    """Build the session package ZIP for an account.

    Returns ``(zip_bytes, filename)`` where the ZIP holds exactly
    ``{base}.session`` (Telethon SQLite, version 7) and ``{base}.json``
    (metadata incl. app_id/app_hash/id/username/phone). Raises
    ``ValueError`` with ``ACCOUNT_NOT_FOUND`` / ``SESSION_NOT_EXPORTABLE``
    / ``CREDENTIALS_RESOLUTION_FAILED`` on failure.
    """
    account_name = validate_storage_name(account_name, field_name="account_name")
    if session_dir is None:
        session_dir = get_settings().resolve_session_dir()
    if not (session_dir / f"{account_name}.session").is_file():
        raise ValueError("ACCOUNT_NOT_FOUND")

    auth = _read_auth(account_name, session_dir)

    try:
        api_id, api_hash = resolve_telegram_api_credentials(
            get_config_service().get_telegram_config(),
            env_api_id=os.getenv("TG_API_ID"),
            env_api_hash=os.getenv("TG_API_HASH"),
        )
    except Exception as exc:
        raise ValueError(f"CREDENTIALS_RESOLUTION_FAILED: {exc}") from exc

    profile = get_account_profile(account_name) or {}
    user_id = int(auth["user_id"])
    base = _clean_base_name(profile.get("username"), account_name, user_id)
    session_bytes = _build_telethon_session_bytes(auth)
    metadata = {
        "session_file": f"{base}.session",
        "id": user_id,
        "app_id": int(auth.get("api_id") or api_id),
        "app_hash": str(api_hash),
        "username": profile.get("username"),
        "first_name": profile.get("first_name"),
        "last_name": None,
        "phone": profile.get("phone"),
        "exported_by": exported_by,
        "exported_at": utc_now_iso_z(),
        "note": _EXPORT_NOTE,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{base}.session", session_bytes)
        zf.writestr(
            f"{base}.json",
            json.dumps(metadata, ensure_ascii=False, indent=2),
        )
    logger.info("Built session package for account %s (base=%s)", account_name, base)
    return buf.getvalue(), f"{base}.zip"
