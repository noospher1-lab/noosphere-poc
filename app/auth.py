"""
Accounts for the PoC: username + password (scrypt from the stdlib — no new
dependency), opaque session tokens stored in Postgres, HTTP-only cookie.

The structural point of accounts here: the AUTHOR OF EVERY WRITE comes from the
session, never from the request body. Until now the client claimed any author
it liked (fine for a solo test bench, unusable with real people).

Deliberately NOT here (test scale, ~100 users): email confirmation, password
reset, OAuth, rate limiting, CSRF tokens (same-site=lax cookie covers the PoC).
"""

import hashlib
import os
import secrets

# scrypt cost parameters — interactive-login grade
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return salt.hex() + "$" + h.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, h_hex = stored.split("$", 1)
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                           n=_N, r=_R, p=_P)
        return secrets.compare_digest(h.hex(), h_hex)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)
