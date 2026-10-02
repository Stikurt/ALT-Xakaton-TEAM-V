"""Password hashes with stdlib scrypt (RFC 7914, memory-hard). No extra dependency.

Encoded form, safe for .env, PowerShell and docker compose (no '$' or '='):
    scrypt:<log2 N>:<r>:<p>:<salt base64url>:<key base64url>
"""
import base64
import binascii
import hashlib
import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass

PREFIX = "scrypt"
# OWASP Password Storage Cheat Sheet profile N=2^15, r=8, p=3: about 32 MiB per check.
DEFAULT_LOG_N, DEFAULT_R, DEFAULT_P = 15, 8, 3
# N*p for r=8 must not be weaker than the cheapest OWASP profile (N=2^13, p=10).
MIN_COST = 81920
MAX_MEMORY = 256 * 1024 * 1024
SALT_BYTES, KEY_BYTES = 16, 32
MIN_PASSWORD_CHARS = 12
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidHash(ValueError):
    """Raised for a malformed or too weak hash. The message never contains the value."""


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    if not _B64.fullmatch(text):
        raise InvalidHash("invalid base64url field")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise InvalidHash("invalid base64url field") from exc


def _normalize(password: str) -> bytes:
    # The same visible password must hash identically from a browser and a terminal.
    return unicodedata.normalize("NFC", password).encode("utf-8")


@dataclass(frozen=True)
class ScryptHash:
    log_n: int
    r: int
    p: int
    salt: bytes
    key: bytes

    def encode(self) -> str:
        return f"{PREFIX}:{self.log_n}:{self.r}:{self.p}:{_b64e(self.salt)}:{_b64e(self.key)}"

    def __repr__(self) -> str:  # never print key material
        return f"ScryptHash(log_n={self.log_n}, r={self.r}, p={self.p})"


def parse_hash(value: str) -> ScryptHash:
    parts = value.strip().split(":")
    if len(parts) != 6 or parts[0] != PREFIX:
        raise InvalidHash("expected scrypt:<logN>:<r>:<p>:<salt>:<key>")
    try:
        log_n, r, p = (int(x) for x in parts[1:4])
    except ValueError as exc:
        raise InvalidHash("cost parameters must be integers") from exc
    if not (14 <= log_n <= 20 and 8 <= r <= 32 and 1 <= p <= 16):
        raise InvalidHash("cost parameters out of the accepted range")
    if (1 << log_n) * p < MIN_COST or 128 * r * (1 << log_n) > MAX_MEMORY:
        raise InvalidHash("cost parameters are too weak or need too much memory")
    salt, key = _b64d(parts[4]), _b64d(parts[5])
    if len(salt) < SALT_BYTES or not 32 <= len(key) <= 64:
        raise InvalidHash("salt or key has an unexpected length")
    return ScryptHash(log_n, r, p, salt, key)


def _derive(password: str, log_n: int, r: int, p: int, salt: bytes, length: int) -> bytes:
    return hashlib.scrypt(_normalize(password), salt=salt, n=1 << log_n, r=r, p=p,
                          maxmem=MAX_MEMORY + (16 << 20), dklen=length)


def hash_password(password: str, *, log_n=DEFAULT_LOG_N, r=DEFAULT_R, p=DEFAULT_P) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    encoded = ScryptHash(log_n, r, p, salt, _derive(password, log_n, r, p, salt, KEY_BYTES)).encode()
    parse_hash(encoded)  # refuse to emit parameters the server would reject
    return encoded


def verify_password(password: str, expected: ScryptHash) -> bool:
    actual = _derive(password, expected.log_n, expected.r, expected.p, expected.salt, len(expected.key))
    return hmac.compare_digest(actual, expected.key)
