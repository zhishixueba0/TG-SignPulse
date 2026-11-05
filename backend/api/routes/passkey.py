"""KeePassXC passkey login for TG-SignPulse (fork patch).

This is NOT upstream code. It was added at the owner's request, mirroring
the server-side passkey flow in his telegram-manager panel:

  * The user pastes three fields from a KeePassXC / KeePassDX entry
    (KPEX_PASSKEY_*), in the panel, over the SSH tunnel only:
      credential_id = KPEX_PASSKEY_CREDENTIAL_ID (base64url, as-is)
      private_key_pem = KPEX_PASSKEY_PRIVATE_KEY_PEM (P-256 PKCS#8 PEM)
      user_handle = KPEX_PASSKEY_USER_HANDLE (base64url, or "DC:userId")
  * The server does Telethon init -> local ECDSA sign (origin
    https://telegram.org, flags 0x1d, signCount 0) -> finish, all in
    memory. The private key is used once to sign and is never stored
    in the DB, in logs, or in the pending 2FA state.
  * On success the Telethon .session file is converted with
    session_importer.import_telethon_sqlite_session() and verified with
    session_importer.verify_imported_session() (real Kurigram get_me
    check), which persists the Pyrogram session and creates the
    account. No Pyrogram passkey support exists, so Telethon is the
    passkey client and the import path is the converter.

API credentials: form api_id/api_hash win; otherwise the panel's
/config/telegram credentials are used (env TG_API_ID/TG_API_HASH
first, then the panel config, which falls back to the upstream
shared public default if the owner has not set his own). The
response reports api_is_custom so the UI can warn when the shared
default is in use. Never send api_id/api_hash in chat; set them
in the panel's Telegram settings.

Proxy: optional proxy URL string (the same format the panel stores
per account). socks5 / socks4 / http / https, plus mtproto
(mtproto://<secret>@host:port or a tg://proxy? share link).
MTProto uses Telethon's native MTProxy connection class; Telethon
has no fake-TLS, so an ee secret is used as a plain obfuscated2 key
(its 16-byte key part) -- Kurigram logins support ee properly,
passkey login through an ee proxy is best-effort and fails closed
if the proxy rejects it. A proxy failure is a failure, never a
silent fallback to direct.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.core.auth import get_current_user
from backend.core.config import get_settings
from backend.models.user import User
from backend.services.config import get_config_service
from backend.services.telegram.credentials import (
    resolve_telegram_api_credentials,
)
from backend.services.telegram.session_importer import (
    import_telethon_sqlite_session,
    verify_imported_session,
)
from backend.utils.names import validate_storage_name
from backend.utils.proxy import build_proxy_dict
from backend.utils.tg_session import list_account_names, set_account_profile

router = APIRouter()
logger = logging.getLogger("backend.passkey_api")

# Pending 2FA logins: passkey_id -> state. The Telethon client and its
# temp session dir are kept ONLY until the second step completes or the
# entry expires (10 minutes). The private key is never stored here.
_pending: dict[str, dict] = {}
_PENDING_TTL_SECONDS = 600


def _prune_pending() -> None:
    now = time.time()
    expired = [
        pid
        for pid, entry in _pending.items()
        if now - entry.get("created_at", 0) > _PENDING_TTL_SECONDS
    ]
    for pid in expired:
        entry = _pending.pop(pid)
        try:
            client = entry.get("client")
            if client is not None:
                # Best effort only; the event loop may already be gone.
                import asyncio

                try:
                    asyncio.get_running_loop().create_task(client.disconnect())
                except RuntimeError:
                    pass
        except Exception:
            pass
        try:
            shutil.rmtree(entry.get("tmpdir", ""), ignore_errors=True)
        except Exception:
            pass


def _b64url_decode(s: str) -> bytes:
    s = (s or "").replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def _parse_user_handle(raw: str) -> str:
    s = (raw or "").strip()
    if ":" in s:
        dc, _uid = s.split(":", 1)
        int(dc)
        return s
    decoded = _b64url_decode(s).decode("utf-8")
    dc, _uid = decoded.split(":", 1)
    int(dc)
    return decoded


def _build_server_assertion(private_key_pem: str, challenge_b64url: str, rp_id: str):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    if rp_id != "telegram.org":
        raise ValueError(f"Refusing to sign for non-telegram.org RP: {rp_id!r}")
    client_data = {
        "type": "webauthn.get",
        "challenge": challenge_b64url,
        "origin": f"https://{rp_id}",
        "crossOrigin": False,
    }
    client_data_json = json.dumps(client_data, separators=(",", ":")).encode("utf-8")
    client_data_hash = hashlib.sha256(client_data_json).digest()
    rp_id_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
    flags = 0x01 | 0x04 | 0x08 | 0x10  # UP + UV + BS + BE = 0x1d
    authenticator_data = rp_id_hash + bytes([flags]) + (0).to_bytes(4, "big")
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise ValueError("Private key is not an EC P-256 private key")
    signature = private_key.sign(
        authenticator_data + client_data_hash, ec.ECDSA(hashes.SHA256())
    )
    return client_data_json.decode("utf-8"), authenticator_data, signature


def _tg_error(e: Exception) -> str:
    from telethon.errors import FloodWaitError, SessionPasswordNeededError

    if isinstance(e, FloodWaitError):
        return (
            f"Telegram limited, try again in {e.seconds} seconds, "
            "do not retry repeatedly"
        )
    if isinstance(e, SessionPasswordNeededError):
        return (
            "This account has two-step verification enabled, "
            "please enter the 2FA password"
        )
    msg = str(e)
    table = {
        "PHONE_CODE_INVALID": "Code is incorrect",
        "PASSWORD_HASH_INVALID": "Two-step verification password is incorrect",
        "API_ID_INVALID": "API ID is invalid",
        "API_HASH_INVALID": "API Hash is invalid",
        "USER_DEACTIVATED": "This account is deactivated",
        "AUTH_KEY_UNREGISTERED": "Session is invalid",
        "PASSKEY_ORIGIN_MISMATCH": "Passkey origin mismatch",
        "PASSKEY_CREDENTIAL_NOT_FOUND": "Passkey credential not found for this account",
    }
    for k, v in table.items():
        if k in msg:
            return v
    if (
        "0 bytes read" in msg
        or "IncompleteReadError" in msg
        or "Server closed the connection" in msg
    ):
        return (
            "Server closed the connection: proxy disconnected, DC switch "
            "failed or network interrupted. Try direct, or another proxy, "
            "once -- do not retry repeatedly"
        )
    return msg or type(e).__name__


def _resolve_api(body_api_id, body_api_hash):
    if body_api_id or body_api_hash:
        if not body_api_id or body_api_hash:
            raise HTTPException(
                status_code=400,
                detail="api_id and api_hash must be given together, or both "
                "left empty to use the panel Telegram config",
            )
        return int(body_api_id), body_api_hash, True
    try:
        tg_config = get_config_service().get_telegram_config()
        api_id, api_hash = resolve_telegram_api_credentials(
            tg_config,
            env_api_id=os.getenv("TG_API_ID"),
            env_api_hash=os.getenv("TG_API_HASH"),
        )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Telegram API ID / API Hash is not configured. Set your "
            "own in Settings > Telegram first (my.telegram.org), or fill "
            "api_id / api_hash in this form.",
        )
    is_custom = bool(tg_config.get("is_custom"))
    return api_id, api_hash, is_custom


def _telethon_proxy_kwargs(proxy_url: Optional[str]):
    """Map a panel proxy URL string to Telethon client kwargs.

    socks5 / socks4 / http / https use Telethon's proxy dict;
    mtproto uses Telethon's native MTProxy connection class with the
    raw secret string (Telethon normalizes ee/dd prefixes itself).
    A proxy failure is a failure, never a silent fallback to direct.
    """
    if not proxy_url:
        return {}
    d = build_proxy_dict(proxy_url)
    if d is None:
        raise HTTPException(
            status_code=400,
            detail="Proxy URL is invalid, expected scheme://[user:pass@]host:port",
        )
    scheme = str(d.get("scheme") or "")
    if scheme == "mtproxy":
        # Telethon has no fake-TLS: an ee secret is used as a plain
        # obfuscated2 key (its 16-byte key part). Kurigram logins
        # support ee properly; passkey login through an ee proxy is
        # best-effort and fails closed if the proxy rejects it.
        from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate

        return {
            "proxy": (d["hostname"], int(d["port"]), str(d["secret"])),
            "connection": ConnectionTcpMTProxyRandomizedIntermediate,
        }
    if scheme not in ("socks5", "socks4", "http", "https"):
        raise HTTPException(
            status_code=400,
            detail=f"Passkey login does not support proxy type {scheme or 'unknown'}",
        )
    kwargs = {
        "proxy_type": scheme,
        "addr": d["hostname"],
        "port": int(d["port"]),
        "rdns": True,
    }
    if d.get("username"):
        kwargs["username"] = d["username"]
    if d.get("password"):
        kwargs["password"] = d["password"]
    return {"proxy": kwargs}


async def _complete_login(
    client,
    session_path: str,
    api_id: int,
    api_hash: str,
    me,
    account_name: Optional[str],
    proxy_url: Optional[str],
    remark: Optional[str],
    is_custom: bool,
) -> dict:
    """Finish a successful Telethon passkey login: convert the session,
    verify it with a real Kurigram get_me, persist it, create the
    account. The Telethon client is disconnected first so the session
    file is flushed to disk."""
    try:
        await client.disconnect()
    except Exception:
        pass
    session_file = Path(session_path + ".session")
    if not session_file.exists():
        raise HTTPException(
            status_code=400,
            detail="Telethon session file was not created, please try again",
        )
    raw_bytes = session_file.read_bytes()

    name = (account_name or "").strip()
    if not name:
        phone = getattr(me, "phone", None)
        name = phone or f"passkey-{int(me.id)}"
    try:
        name = validate_storage_name(name, field_name="account_name")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    target_dir = get_settings().resolve_session_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    if (target_dir / f"{name}.session").exists() or name in list_account_names():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Account already exists: {name}",
        )

    temp_path = import_telethon_sqlite_session(name, raw_bytes, target_dir)
    try:
        res = await verify_imported_session(
            account_name=name,
            temp_session_path=temp_path,
            target_dir=target_dir,
            proxy=proxy_url,
            api_id=api_id,
            api_hash=api_hash,
        )
    except ValueError as e:
        err_msg = str(e)
        if "IMPORTED_SESSION_UNAUTHORIZED" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="IMPORTED_SESSION_UNAUTHORIZED",
            )
        raise HTTPException(status_code=400, detail=err_msg)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if remark:
        try:
            set_account_profile(name, remark=remark)
        except Exception:
            logger.warning("passkey login: failed to save remark for %s", name)

    return {
        "success": True,
        "account_name": res.get("account_name", name),
        "user_id": res.get("user_id"),
        "first_name": res.get("first_name"),
        "username": res.get("username"),
        "message": "Passkey login succeeded",
        "api_is_custom": is_custom,
    }


class PasskeyLoginIn(BaseModel):
    credential_id: str = Field(min_length=10, max_length=500)
    private_key_pem: str = Field(min_length=20, max_length=5000)
    user_handle: str = Field(min_length=3, max_length=500)
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    proxy: Optional[str] = None
    account_name: Optional[str] = None
    remark: Optional[str] = None
    password: Optional[str] = None  # 2FA password, only if the account has one


class PasskeyPasswordIn(BaseModel):
    passkey_id: str = Field(min_length=6, max_length=64)
    password: str = Field(min_length=1, max_length=500)


@router.get("/status")
def passkey_status(current_user: User = Depends(get_current_user)):
    try:
        import telethon

        telethon_version = telethon.__version__
    except Exception:
        telethon_version = None
    try:
        is_custom = bool(get_config_service().get_telegram_config().get("is_custom"))
    except Exception:
        is_custom = False
    return {
        "ok": True,
        "passkey_available": telethon_version is not None,
        "telethon_version": telethon_version,
        "api_is_custom": is_custom,
        "proxy_supported": ["socks5", "socks4", "http", "https", "mtproto"],
    }


@router.post("/login")
async def passkey_login(
    body: PasskeyLoginIn, current_user: User = Depends(get_current_user)
):
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.tl import functions as tg_functions
    from telethon.tl import types as tg_types

    pem = (body.private_key_pem or "").strip()
    if "PRIVATE KEY" not in pem:
        raise HTTPException(
            status_code=400,
            detail="Private key format is wrong, paste the full PEM including "
            "-----BEGIN PRIVATE KEY----- and -----END PRIVATE KEY----- lines",
        )
    try:
        user_handle_text = _parse_user_handle(body.user_handle)
        user_dc_id = int(user_handle_text.split(":", 1)[0])
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="userHandle format is wrong, paste "
            "KPEX_PASSKEY_USER_HANDLE from KeePass (base64url), or the "
            "decoded DC:userId form",
        )
    api_id, api_hash, is_custom = _resolve_api(body.api_id, body.api_hash)
    proxy_kwargs = _telethon_proxy_kwargs(body.proxy)
    _prune_pending()

    tmpdir = tempfile.mkdtemp(prefix="tg-passkey-", dir="/tmp")
    session_path = os.path.join(tmpdir, "passkey")
    client = TelegramClient(session_path, api_id, api_hash, **proxy_kwargs)
    init_dc_id = None
    # Set only when returning the 2FA pending response: the client and
    # its temp session must survive this call, so the finally block
    # below must skip disconnect + tmpdir cleanup in that case.
    keep_pending = False
    try:
        await client.connect()
        if await client.is_user_authorized():
            raise HTTPException(
                status_code=400,
                detail="This session is already logged in, use a different account",
            )
        result = await client(
            tg_functions.auth.InitPasskeyLoginRequest(api_id=api_id, api_hash=api_hash)
        )
        options_raw = result.options.data if hasattr(result, "options") else None
        if not options_raw:
            raise RuntimeError("Telegram did not return passkey options")
        public_key = json.loads(options_raw).get("publicKey") or {}
        rp_id = public_key.get("rpId", "telegram.org")
        challenge = public_key.get("challenge", "")
        if not challenge:
            raise RuntimeError("Telegram did not return a passkey challenge")
        init_dc_id = getattr(client.session, "dc_id", None)
        init_auth_key_id = None
        try:
            ak = getattr(client.session, "auth_key", None)
            init_auth_key_id = getattr(ak, "key_id", None) if ak else None
        except Exception:
            pass
        # Telethon reads AuthKey.key_id unsigned (0..2^64-1) but
        # FinishPasskeyLoginRequest packs from_auth_key_id as signed '<q'.
        # Convert >=2^63 to the signed equivalent (same 8 bytes).
        if init_auth_key_id is not None:
            init_auth_key_id = int(init_auth_key_id)
            if init_auth_key_id >= 2**63:
                init_auth_key_id -= 2**64
        # Private key is used once here to sign, then dropped. It is
        # never stored in the pending state, the DB, or logs.
        client_data_text, authenticator_data, signature = _build_server_assertion(
            pem, challenge, rp_id
        )
        pem = None
        tl_cred = tg_types.InputPasskeyCredentialPublicKey(
            id=body.credential_id.strip(),
            raw_id=body.credential_id.strip(),
            response=tg_types.InputPasskeyResponseLogin(
                client_data=tg_types.DataJSON(data=client_data_text),
                authenticator_data=authenticator_data,
                signature=signature,
                user_handle=user_handle_text,
            ),
        )
        try:
            if (
                init_dc_id is not None
                and init_auth_key_id is not None
                and user_dc_id != init_dc_id
            ):
                try:
                    await client._switch_dc(user_dc_id)
                except Exception as _sw_e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Switching to the account DC{user_dc_id} failed "
                        f"(initial DC{init_dc_id}): {_tg_error(_sw_e)}. Check "
                        "the proxy, or retry once with direct.",
                    )
                auth = await client(
                    tg_functions.auth.FinishPasskeyLoginRequest(
                        credential=tl_cred,
                        from_dc_id=init_dc_id,
                        from_auth_key_id=init_auth_key_id,
                    )
                )
            else:
                auth = await client(
                    tg_functions.auth.FinishPasskeyLoginRequest(credential=tl_cred)
                )
            me = getattr(auth, "user", None) or await client.get_me()
        except SessionPasswordNeededError:
            if body.password:
                me = await client.sign_in(password=body.password)
            else:
                passkey_id = uuid.uuid4().hex[:12]
                _pending[passkey_id] = {
                    "client": client,
                    "tmpdir": tmpdir,
                    "session_path": session_path,
                    "api_id": api_id,
                    "api_hash": api_hash,
                    "proxy": body.proxy,
                    "account_name": body.account_name,
                    "remark": body.remark,
                    "is_custom": is_custom,
                    "created_at": time.time(),
                }
                keep_pending = True
                return {
                    "need_password": True,
                    "passkey_id": passkey_id,
                    "message": "Passkey accepted, this account has two-step "
                    "verification, please submit the 2FA password",
                    "api_is_custom": is_custom,
                }
        return await _complete_login(
            client,
            session_path,
            api_id,
            api_hash,
            me,
            body.account_name,
            body.proxy,
            body.remark,
            is_custom,
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback as _tb

        logger.warning(
            "passkey login failed user_dc=%s init_dc=%s: %s",
            user_dc_id,
            init_dc_id,
            e,
        )
        _tb.print_exc()
        raise HTTPException(status_code=400, detail=_tg_error(e))
    finally:
        if not keep_pending:
            try:
                await client.disconnect()
            except Exception:
                pass
            # Remove the temp Telethon session; the imported Pyrogram
            # session now lives in SignPulse's own session dir.
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


@router.post("/password")
async def passkey_password(
    body: PasskeyPasswordIn, current_user: User = Depends(get_current_user)
):
    _prune_pending()
    pending = _pending.pop(body.passkey_id, None)
    if not pending:
        raise HTTPException(
            status_code=400,
            detail="Passkey login has expired, please start again",
        )
    client = pending["client"]
    tmpdir = pending["tmpdir"]
    session_path = pending["session_path"]
    try:
        me = await client.sign_in(password=body.password)
        return await _complete_login(
            client,
            session_path,
            pending["api_id"],
            pending["api_hash"],
            me,
            pending.get("account_name"),
            pending.get("proxy"),
            pending.get("remark"),
            bool(pending.get("is_custom", False)),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=_tg_error(e))
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
