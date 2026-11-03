"""The codes of an authenticator app (D-146): the second step of every login when AP Coder runs as the company's
server. Any app that follows RFC 6238 works (Microsoft Authenticator, Google Authenticator and others).

* **The codes**: TOTP (RFC 6238 over RFC 4226's HOTP): HMAC-SHA1 of the number of 30-second steps since 1970, cut to
  6 digits, from the standard library. A code of the step before or after the server's own is taken too (a phone's
  clock a little off), never one of a step at or before the last one used (``matching_step``): a code seen over
  someone's shoulder can't be used again.
* **The secret**: 20 random bytes (``secrets``), shown to the person once as base32 and in the QR code of its
  ``otpauth://`` address (``otpauth_uri``), while they set the app up.
* **Kept sealed**: the database keeps each secret sealed (``seal_secret``: HMAC-SHA256 as the stream and the tag)
  with this server's key, ``authenticator.key`` in the data folder (``load_key``), never in the database: a copy of
  the database alone gives no one's codes away. The key is made at the first set-up, readable by the account
  running AP Coder only; it is in the whole-state backups and a restore puts it back only where there is none (a
  new computer), never over the one here (``core/store/backup_files.py``).
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import hmac
import os
import secrets
import struct
from pathlib import Path
from urllib.parse import quote

STEP = 30  # seconds a code lasts
DIGITS = 6
DRIFT = 1  # steps either side of the server's own taken
SECRET_BYTES = 20
ISSUER = "AP Coder"
KEY_FILE = "authenticator.key"  # in the data folder, beside the database
KEY_BYTES = 32
PURPOSE = b"authenticator"


# --- The codes (RFC 4226, RFC 6238) -----------------------------------------------------------------------------------


def hotp(key: bytes, counter: int, digits: int = DIGITS, algorithm: str = "sha1") -> str:
    """RFC 4226: the HMAC of the counter (8 bytes, big-endian), cut at the offset its last 4 bits give."""
    mac = hmac.new(key, struct.pack(">Q", counter), getattr(hashlib, algorithm)).digest()
    offset = mac[-1] & 0x0F
    number = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


def totp(key: bytes, now: float, digits: int = DIGITS, algorithm: str = "sha1") -> str:
    """RFC 6238: the HOTP of the 30-second steps since 1970 at ``now`` (epoch seconds)."""
    return hotp(key, int(now // STEP), digits, algorithm)


def new_secret() -> str:
    """A new secret for one account: 160 random bits, base32 (32 letters and digits, as the apps take it)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")


def _key_of(secret: str) -> bytes:
    text = "".join(str(secret).split()).upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def code(secret: str, now: float) -> str:
    """The code an app holding ``secret`` (base32) shows at ``now``."""
    return totp(_key_of(secret), now)


def matching_step(secret: str, typed: str, now: float, after: int = 0) -> int | None:
    """The step whose code ``typed`` is (spaces in it ignored), within ``DRIFT`` steps of ``now`` and later than
    ``after`` (the last step used); None when there is none. Each code is compared in constant time."""
    typed = "".join(str(typed or "").split())
    if len(typed) != DIGITS or not typed.isascii() or not typed.isdigit():
        return None
    key, here = _key_of(secret), int(now // STEP)
    found = None
    for step in range(here - DRIFT, here + DRIFT + 1):
        if hmac.compare_digest(hotp(key, step), typed) and step > after:
            found = step
    return found


def otpauth_uri(username: str, secret: str) -> str:
    """The address an app reads from the QR code: the issuer "AP Coder" and the username name the account in it."""
    issuer = quote(ISSUER, safe="")
    return f"otpauth://totp/{issuer}:{quote(username, safe='')}?secret={secret}&issuer={issuer}"


# --- Sealing (also the Users page's temporary passwords, D-111) -------------------------------------------------------


def _stream(secret: bytes, purpose: bytes, nonce: bytes, size: int) -> bytes:
    key = hmac.new(secret, purpose + b"-stream", hashlib.sha256).digest()
    return b"".join(hmac.new(key, nonce + n.to_bytes(4, "big"), hashlib.sha256).digest() for n in range(size // 32 + 1))


def _tag(secret: bytes, purpose: bytes, data: bytes) -> bytes:
    return hmac.new(hmac.new(secret, purpose + b"-tag", hashlib.sha256).digest(), data, hashlib.sha256).digest()


def seal(secret: bytes, data: bytes, purpose: bytes) -> bytes:
    """``data`` encrypted and authenticated with ``secret`` (HMAC-SHA256 as the stream and the tag), for ``purpose``
    alone: ``nonce (16) + tag (32) + body``."""
    nonce = secrets.token_bytes(16)
    body = bytes(a ^ b for a, b in zip(data, _stream(secret, purpose, nonce, len(data)), strict=False))
    return nonce + _tag(secret, purpose, nonce + body) + body


def unseal(secret: bytes, sealed: bytes, purpose: bytes) -> bytes | None:
    """What ``seal`` sealed; None when it was sealed with another secret or purpose, or changed since."""
    nonce, tag, body = sealed[:16], sealed[16:48], sealed[48:]
    if len(tag) != 32 or not hmac.compare_digest(tag, _tag(secret, purpose, nonce + body)):
        return None
    return bytes(a ^ b for a, b in zip(body, _stream(secret, purpose, nonce, len(body)), strict=False))


def seal_secret(key: bytes, secret: str) -> str:
    """An account's secret as the database keeps it (base64 text)."""
    return base64.b64encode(seal(key, secret.encode("ascii"), PURPOSE)).decode("ascii")


def open_secret(key: bytes | None, kept: str) -> str | None:
    """The secret ``seal_secret`` kept; None without the key it was sealed with, or for nothing kept."""
    if not key or not kept:
        return None
    try:
        opened = unseal(key, base64.b64decode(kept, validate=True), PURPOSE)
    except (ValueError, binascii.Error):
        return None
    return opened.decode("ascii") if opened is not None else None


# --- This server's key -----------------------------------------------------------------------------------------------


def _read_key(path: Path) -> bytes | None:
    try:
        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return key if len(key) == KEY_BYTES else None


def load_key(folder: Path, create: bool = False) -> bytes | None:
    """This server's key (``KEY_FILE`` in the data folder ``folder``); ``create``: made when there is none, readable
    by this account only, never over one there (two web workers making it at once keep the first). None when there
    is none (or it is damaged) and ``create`` is not asked."""
    path = Path(folder) / KEY_FILE
    if path.exists() or not create:
        return _read_key(path)
    part = path.with_name(f".{KEY_FILE}.{secrets.token_hex(4)}.part")
    handle = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="ascii") as out:
            out.write(secrets.token_bytes(KEY_BYTES).hex() + "\n")
        with contextlib.suppress(FileExistsError):
            os.link(part, path)  # only where there is none: never replaces the key the secrets are sealed with
    finally:
        part.unlink(missing_ok=True)
    return _read_key(path)
