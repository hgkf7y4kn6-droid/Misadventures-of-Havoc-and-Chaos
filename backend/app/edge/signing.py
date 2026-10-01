"""HMAC request signing between the engine and the Cloudflare edge (both directions).

canonical = f"{timestamp}.{METHOD}.{path}.{sha256_hex(body)}"
headers:   x-havoc-ts: <unix seconds>   x-havoc-sig: hex(hmac_sha256(secret, canonical))

Mirrored in ``workers/edge/src/signing.ts``.
"""

from __future__ import annotations

import hashlib
import hmac
import time

MAX_SKEW_SECONDS = 300


def canonical(ts: str, method: str, path: str, body: bytes) -> str:
    return f"{ts}.{method.upper()}.{path}.{hashlib.sha256(body).hexdigest()}"


def sign(secret: str, method: str, path: str, body: bytes, ts: int | None = None) -> dict[str, str]:
    t = str(ts if ts is not None else int(time.time()))
    sig = hmac.new(secret.encode(), canonical(t, method, path, body).encode(), hashlib.sha256).hexdigest()
    return {"x-havoc-ts": t, "x-havoc-sig": sig}


def verify(secret: str, method: str, path: str, body: bytes, ts: str | None, sig: str | None) -> bool:
    if not ts or not sig:
        return False
    try:
        if abs(time.time() - int(ts)) > MAX_SKEW_SECONDS:
            return False
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), canonical(ts, method, path, body).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
