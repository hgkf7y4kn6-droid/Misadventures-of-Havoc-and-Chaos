"""Identity: who is calling.

Three credential types, identical in the standalone server and the Cloudflare
edge Worker (``workers/edge/src/auth.ts``) so clients never care which one
they talk to:

* **Clerk session JWT** (``Authorization: Bearer <jwt>``) — signed-in users.
  Verified locally (RS256) against Clerk's JWKS or a pinned PEM key; ``sub``
  becomes the user id.
* **Guest session** (``Authorization: Bearer g.<payload>.<sig>``) — party
  guests who don't want an account. HMAC-signed by the server.
* **Socket ticket** (``/ws/{code}?ticket=t.<payload>.<sig>``) — a 60-second,
  single-game credential minted from either of the above, so long-lived
  tokens never appear in URLs.

Token format (both ``g.`` and ``t.``): ``<prefix>.<base64url(json)>.<base64url(hmac_sha256(secret, prefix + "." + payload))>``
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache

from .config import Settings


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    user_id: str
    kind: str  # "clerk" | "guest"
    name: str | None = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_token(secret: str, prefix: str, claims: dict) -> str:
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(secret.encode(), f"{prefix}.{payload}".encode(), hashlib.sha256).digest()
    return f"{prefix}.{payload}.{_b64(mac)}"


def read_token(secret: str, prefix: str, token: str) -> dict:
    try:
        got_prefix, payload, sig = token.split(".")
    except ValueError as exc:
        raise AuthError("malformed token") from exc
    if got_prefix != prefix:
        raise AuthError("wrong token type")
    expected = hmac.new(secret.encode(), f"{prefix}.{payload}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _unb64(sig)):
        raise AuthError("bad signature")
    claims = json.loads(_unb64(payload))
    if claims.get("exp") and claims["exp"] < time.time():
        raise AuthError("token expired")
    return claims


class IdentityService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.secret = settings.session_secret or secrets.token_urlsafe(32)
        if not settings.session_secret:
            # Fine for a single dev process; production must pin a secret so tokens survive restarts.
            import logging

            logging.getLogger("havoc.identity").warning("HAVOC_SESSION_SECRET not set; using an ephemeral secret")

    # -- guests ----------------------------------------------------------------

    def issue_guest(self, name: str) -> tuple[str, Identity, int]:
        exp = int(time.time()) + self.settings.guest_session_days * 86400
        uid = f"guest_{secrets.token_hex(8)}"
        token = sign_token(self.secret, "g", {"sub": uid, "name": name[:32], "exp": exp})
        return token, Identity(uid, "guest", name[:32]), exp

    # -- websocket tickets -------------------------------------------------------

    def issue_ticket(self, user_id: str, code: str, ttl: int = 60) -> str:
        return sign_token(self.secret, "t", {"sub": user_id, "code": code.upper(), "exp": int(time.time()) + ttl})

    def read_ticket(self, ticket: str, code: str) -> str:
        claims = read_token(self.secret, "t", ticket)
        if claims.get("code") != code.upper():
            raise AuthError("ticket is for another game")
        return claims["sub"]

    # -- bearer credentials ----------------------------------------------------------

    def verify(self, bearer: str | None) -> Identity:
        if not bearer:
            raise AuthError("missing credentials")
        if bearer.startswith("g."):
            claims = read_token(self.secret, "g", bearer)
            return Identity(claims["sub"], "guest", claims.get("name"))
        if bearer.count(".") == 2 and (self.settings.clerk_jwks_url or self.settings.clerk_jwt_key):
            return self._verify_clerk(bearer)
        raise AuthError("unrecognised credentials")

    def _verify_clerk(self, token: str) -> Identity:
        import jwt

        s = self.settings
        try:
            if s.clerk_jwt_key:
                key = s.clerk_jwt_key.replace("\\n", "\n")
            else:
                key = _jwks_client(s.clerk_jwks_url).get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], issuer=s.clerk_issuer or None,
                                options={"require": ["exp", "sub"], "verify_aud": False}, leeway=5)
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid session: {exc}") from exc
        # Clerk's recommended CSRF protection: the azp (origin) must be one we expect.
        if s.clerk_authorized_parties and claims.get("azp") and claims["azp"] not in s.clerk_authorized_parties:
            raise AuthError("unexpected authorized party")
        return Identity(claims["sub"], "clerk", claims.get("name") or claims.get("username"))


@lru_cache(maxsize=4)
def _jwks_client(url: str):
    import jwt

    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
