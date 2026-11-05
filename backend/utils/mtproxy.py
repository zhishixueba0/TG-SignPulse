"""MTProxy support for TG-SignPulse (fork patch).

Kurigram 2.2.26 already speaks MTProxy natively -- this is NOT the
old Telethon TCP monkeypatch from the v1.0.130 panel, which is
obsolete here. Kurigram ships ``pyrogram.connection.proxy.MTProxy``
and ``TCP._connect_via_mtproxy`` (obfuscated2 handshake, dd random
padding, ee fake-TLS), and ``Client(proxy={...})`` accepts
``dict(scheme="mtproxy", hostname=..., port=..., secret="...")`` or a
``tg://proxy?server&port&secret`` link. What was missing was only
the wiring in this panel:

* ``backend/utils/proxy.py`` rejected the ``mtproto`` scheme outright
  (``SUPPORTED_PROXY_SCHEMES``), so no account could store one;
* the exit-IP probe (HTTP endpoints) cannot run through an MTProxy,
  so the probe needs a real handshake check instead.

This module holds the MTProxy-specific pieces:

* ``normalize_mtproto_secret`` -- validate a user-typed secret
  (hex / base64 / base64url, plain 16 bytes, dd 17 bytes, ee with
  SNI domain), mirroring Kurigram/TDLib rules. Returns the original
  encoded string; Kurigram decodes it again when building the
  client, so the ee domain is preserved (stripping it would break
  fake-TLS).
* ``parse_mtproxy_link`` -- ``tg://proxy?`` / ``t.me/proxy?`` share
  links to the canonical ``mtproto://<secret>@<host>:<port>`` URL.
* ``probe_mtproxy_handshake`` -- connect a Kurigram transport to
  DC1 through the proxy and confirm the proxy keeps the connection
  open after the header. A proxy that rejects the secret closes the
  connection immediately, so a bare TCP port-open is never reported
  as "proxy works".

The secret is never logged and never returned by any API; it lives
only in the per-account proxy string the panel already stores, and
in the Kurigram client built from it.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import re
import urllib.parse
from typing import Optional

logger = logging.getLogger("backend.utils.mtproxy")

# Kurigram PROD DC1, pyrogram/storage/sqlite_storage.py PROD table.
# The handshake probe only needs one reachable DC address.
_DC1_ADDRESS: tuple[str, int] = ("149.154.175.53", 443)

# TDLib's MAX_DOMAIN_LENGTH for the SNI domain an ee secret carries.
_MAX_SNI_DOMAIN_SIZE = 182

# Telegram proxy share links, same shapes Kurigram itself accepts
# (pyrogram/connection/proxy.py _MTPROXY_LINK_RE).
_MTPROXY_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t(?:elegram)?\.(?:org|me|dog)/proxy\?|tg://proxy\?)(.+)",
    re.IGNORECASE,
)


def _decode_secret_bytes(encoded: str) -> bytes:
    """Hex, then base64 in either alphabet -- the encodings TDLib accepts."""
    s = (encoded or "").strip()
    if not s:
        raise ValueError("MTProto secret is empty")
    try:
        return bytes.fromhex(s)
    except ValueError:
        pass
    padded = s + "=" * (-len(s) % 4)
    try:
        return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("MTProto secret must be hex, base64url or base64") from exc


def normalize_mtproto_secret(secret: str) -> str:
    """Validate an MTProxy secret, return the original encoded string.

    Rules mirror Kurigram's ``_decode_mtproxy_secret`` (and TDLib):
    16 bytes is a bare key whatever its first byte happens to be;
    17 bytes must start with the 0xDD padding marker; longer must
    start with the 0xEE fake-TLS marker followed by a 16-byte key and
    a non-empty ASCII SNI domain (max 182 bytes). Raises ValueError
    on anything else. The returned string is the input stripped, so
    Kurigram still sees the full ee secret including its domain.
    """
    raw = _decode_secret_bytes(secret)
    if len(raw) == 16:
        return (secret or "").strip()
    if len(raw) == 17 and raw[0] == 0xDD:
        return (secret or "").strip()
    if len(raw) > 17 and raw[0] == 0xEE:
        domain = raw[17:]
        if not domain:
            raise ValueError("MTProto ee secret carries no SNI domain")
        if len(domain) > _MAX_SNI_DOMAIN_SIZE:
            raise ValueError("MTProto ee secret SNI domain is too long")
        try:
            domain.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("MTProto ee secret SNI domain is not ASCII") from exc
        return (secret or "").strip()
    raise ValueError(
        "MTProto secret must decode to 16 bytes (plain), 17 with a dd "
        "marker, or an ee marker followed by a 16-byte key and a domain"
    )


def parse_mtproxy_link(raw: str) -> Optional[str]:
    """Convert a Telegram MTProxy share link to a canonical mtproto URL.

    Accepts ``tg://proxy?server=&port=&secret=`` and the
    ``t.me`` / ``telegram.me`` / ``telegram.org`` / ``telegram.dog``
    ``/proxy?`` forms. Returns ``mtproto://<secret>@<host>:<port>``
    or None when the input is not a usable MTProxy link (including a
    link whose secret fails validation -- fail closed, never guess).
    """
    value = (raw or "").strip()
    match = _MTPROXY_LINK_RE.match(value)
    if not match:
        return None
    params = urllib.parse.parse_qs(match.group(1))
    host = (params.get("server") or [None])[0]
    port = (params.get("port") or [None])[0]
    secret = (params.get("secret") or [None])[0]
    if not host or not port or not secret:
        return None
    try:
        port_i = int(port)
    except ValueError:
        return None
    if not 1 <= port_i <= 65535:
        return None
    try:
        secret = normalize_mtproto_secret(secret)
    except ValueError:
        return None
    return f"mtproto://{urllib.parse.quote(secret, safe='')}@{host}:{port_i}"


def secret_cache_hash(secret: str) -> str:
    """Short one-way hash of a secret for cache/breaker keys.

    The secret itself never goes into a key, a log line, or a state
    file; two proxies on the same host:port with different secrets
    still get different keys.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


async def probe_mtproxy_handshake(
    proxy_dict: dict, timeout: float = 8.0
) -> tuple[bool, str, Optional[int]]:
    """Prove an MTProxy accepts the handshake, not just the TCP port.

    Builds a Kurigram transport for the proxy (transport class picked
    by Kurigram itself: TCPIntermediatePadded for dd/ee secrets,
    TCPAbridged otherwise) and connects it to DC1. After the 64-byte
    obfuscated2 header is sent, a proxy that rejects the secret
    closes the connection right away; we wait briefly for that EOF.
    Returns (True, "MTProxy handshake OK", connect_ms) only when the
    connection is still open after the wait, (False, reason, None)
    otherwise. ``connect_ms`` is the measured ``transport.connect``
    time only -- the 2.0s recv confirmation wait validates liveness
    but is NOT counted as latency. Never falls back to direct: this
    function only ever talks to the proxy.
    """
    import time

    try:
        from pyrogram.connection.connection import transport_class_for
        from pyrogram.connection.proxy import normalize_proxy
    except Exception as exc:  # pragma: no cover - Kurigram missing
        return False, f"MTProxy probe unavailable: {exc}", None
    try:
        mtproxy = normalize_proxy(proxy_dict)
    except Exception as exc:
        return False, f"MTProxy config invalid: {exc}", None

    transport = transport_class_for(mtproxy)(proxy=mtproxy, dc_id=1)
    try:
        try:
            connect_started = time.monotonic()
            await asyncio.wait_for(transport.connect(_DC1_ADDRESS), timeout=timeout)
            connect_ms = max(int((time.monotonic() - connect_started) * 1000), 0)
        except Exception as exc:
            return (
                False,
                f"MTProxy handshake failed: {type(exc).__name__}: {exc}",
                None,
            )
        try:
            data = await asyncio.wait_for(transport.recv(1), timeout=2.0)
        except asyncio.TimeoutError:
            # Still open after the wait: the proxy accepted the header
            # and is waiting for MTProto packets.
            return True, "MTProxy handshake OK", connect_ms
        if data is None:
            return (
                False,
                "MTProxy closed the connection after the handshake "
                "(wrong secret or unsupported framing)",
                None,
            )
        return True, "MTProxy handshake OK", connect_ms
    finally:
        try:
            await transport.close()
        except Exception:
            pass
        logger.info(
            "MTProxy handshake probe finished for %s:%s",
            mtproxy.hostname,
            mtproxy.port,
        )
