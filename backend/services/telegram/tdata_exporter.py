"""TData export (session -> Telegram Desktop tdata folder), via opentele.

Builds a Telegram Desktop ``tdata`` folder from the account's CURRENT
auth_key (the same material ``session_package.build_session_zip`` copies)
and zips it as ``{base}-tdata.zip`` with a top-level ``tdata/`` folder,
ready to drop into Telegram Desktop's data directory.

The conversion is offline: opentele's ``TDesktop.FromTelethon`` with
``UseCurrentSession`` only reads the Telethon session's auth key / dc /
user id and writes the tdata files (``key_datas`` + account folder);
no network call is made and no new authorization is created. The
account's own API credentials are kept (``UseCurrentSession`` must use
the API that first authorized the session).

``opentele`` is an optional extra (``tdata``): when it is not installed
the export raises ``TDATA_CONVERTER_UNAVAILABLE`` instead of producing
anything -- a missing converter never yields a fake TData package.
"""

from __future__ import annotations

import io
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from backend.core.config import get_settings
from backend.services.config import get_config_service
from backend.services.telegram.credentials import resolve_telegram_api_credentials
from backend.services.telegram.session_package import (
    _build_telethon_session_bytes,
    _clean_base_name,
    _read_auth,
)
from backend.utils.names import validate_storage_name
from backend.utils.tg_session import get_account_profile

logger = logging.getLogger("backend.telegram.tdata_exporter")


def _check_opentele_available() -> tuple:
    """Import opentele's TDesktop pieces, or raise TDATA_CONVERTER_UNAVAILABLE."""
    try:
        from opentele.api import APIData, UseCurrentSession
        from opentele.td import TDesktop
        from opentele.tl import TelegramClient
    except (KeyboardInterrupt, SystemExit):
        raise
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError(
            "TDATA_CONVERTER_UNAVAILABLE: Optional dependency 'opentele' "
            "is not installed"
        ) from exc
    except BaseException as exc:
        raise ValueError(
            "TDATA_CONVERTER_UNAVAILABLE: Optional dependency 'opentele' "
            f"failed to initialize: {exc}"
        ) from exc
    return APIData, UseCurrentSession, TDesktop, TelegramClient


async def build_tdata_zip(
    account_name: str,
    *,
    session_dir: Optional[Path] = None,
    exported_by: Optional[str] = None,
) -> tuple[bytes, str]:
    """Build the TData package ZIP for an account.

    Returns ``(zip_bytes, filename)`` where the ZIP holds a top-level
    ``tdata/`` folder (``key_datas`` + the account folder) produced by
    opentele from the account's current auth_key. Raises ``ValueError``
    with ``ACCOUNT_NOT_FOUND`` / ``SESSION_NOT_EXPORTABLE`` /
    ``CREDENTIALS_RESOLUTION_FAILED`` / ``TDATA_CONVERTER_UNAVAILABLE`` /
    ``TDATA_CONVERSION_FAILED`` on failure.
    """
    _APIData, UseCurrentSession, TDesktop, TelegramClient = _check_opentele_available()
    account_name = validate_storage_name(account_name, field_name="account_name")
    if session_dir is None:
        session_dir = get_settings().resolve_session_dir()
    if not (session_dir / f"{account_name}.session").is_file():
        raise ValueError("ACCOUNT_NOT_FOUND")

    auth = _read_auth(account_name, session_dir)

    import os

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

    with tempfile.TemporaryDirectory(prefix="tg-tdata-export-") as tmpdir:
        tmp = Path(tmpdir)
        session_path = tmp / "export"
        # Telethon appends ".session": write the file it will open.
        (tmp / "export.session").write_bytes(session_bytes)
        client = TelegramClient(
            str(session_path), api_id=int(api_id), api_hash=str(api_hash)
        )
        # The Telethon session already carries dc/auth_key; preset the
        # user id so FromTelethon never needs a network get_me.
        client.UserId = user_id
        try:
            tdesk = await TDesktop.FromTelethon(
                client,
                flag=UseCurrentSession,
                api=_APIData(api_id=int(api_id), api_hash=str(api_hash)),
            )
        except Exception as exc:
            raise ValueError(f"TDATA_CONVERSION_FAILED: {exc}") from exc
        tdata_dir = tmp / "tdata"
        tdata_dir.mkdir(parents=True, exist_ok=True)
        try:
            saved = tdesk.SaveTData(str(tdata_dir))
        except Exception as exc:
            raise ValueError(f"TDATA_CONVERSION_FAILED: {exc}") from exc
        if not saved:
            raise ValueError("TDATA_CONVERSION_FAILED: opentele 保存 tdata 失败")
        if not (tdata_dir / "key_datas").is_file():
            raise ValueError("TDATA_CONVERSION_FAILED: tdata 缺少 key_datas")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(tdata_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, f"tdata/{path.relative_to(tdata_dir)}")
        logger.info("Built TData package for account %s (base=%s)", account_name, base)
        return buf.getvalue(), f"{base}-tdata.zip"


def verify_tdata_zip(zip_bytes: bytes) -> dict:
    """Structural check for a produced TData ZIP (no network).

    Returns ``{"files": [...]}``; raises ``ValueError`` when the shape is
    wrong (no top-level ``tdata/`` folder, no ``key_datas``, no account
    folder). Used by tests; the live round-trip check is importing the
    ZIP back through the TData importer.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    if not names or not all(n.startswith("tdata/") for n in names):
        raise ValueError("TDATA_INVALID_STRUCTURE: 缺少顶层 tdata/ 目录")
    if "tdata/key_datas" not in names:
        raise ValueError("TDATA_INVALID_STRUCTURE: 缺少 key_datas")
    account_dirs = {
        n.split("/")[1]
        for n in names
        if n.count("/") >= 2 and len(n.split("/")[1]) == 16
    }
    if not account_dirs:
        raise ValueError("TDATA_INVALID_STRUCTURE: 缺少账号目录")
    return {"files": names}
