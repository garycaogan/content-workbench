"""Cloudflare Access check for the public-domain setup.

When the workbench is reached through a Cloudflare Tunnel, Access sits in front
and adds a signed ``Cf-Access-Jwt-Assertion`` header to every request. The app
verifies that token itself, so a missing or misconfigured Access policy fails
closed instead of exposing the login page to the internet."""

from __future__ import annotations

import base64
import json
import threading
import time

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
JWKS_TTL = 3600


def _b64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def issuer(team: str) -> str:
    return f"https://{team}.cloudflareaccess.com"


def fetch_keys(team: str) -> dict:
    """kid -> RSA public key, cached for an hour."""
    with _lock:
        hit = _cache.get(team)
        if hit and time.time() - hit[0] < JWKS_TTL:
            return hit[1]
    resp = httpx.get(issuer(team) + "/cdn-cgi/access/certs", timeout=10)
    resp.raise_for_status()
    keys = {}
    for jwk in resp.json().get("keys", []):
        if jwk.get("kty") != "RSA":
            continue
        n = int.from_bytes(_b64(jwk["n"]), "big")
        e = int.from_bytes(_b64(jwk["e"]), "big")
        keys[jwk["kid"]] = rsa.RSAPublicNumbers(e, n).public_key()
    with _lock:
        _cache[team] = (time.time(), keys)
    return keys


class AccessDenied(Exception):
    pass


def verify(token: str, team: str, aud: str, emails: list[str], keys: dict | None = None) -> dict:
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64(head_b64))
        claims = json.loads(_b64(body_b64))
    except Exception:
        raise AccessDenied("malformed token") from None
    if header.get("alg") != "RS256":
        raise AccessDenied("unexpected alg")
    keys = keys if keys is not None else fetch_keys(team)
    key = keys.get(header.get("kid"))
    if key is None:
        raise AccessDenied("unknown key")
    try:
        key.verify(_b64(sig_b64), f"{head_b64}.{body_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        raise AccessDenied("bad signature") from None
    now = time.time()
    if claims.get("iss") != issuer(team):
        raise AccessDenied("wrong issuer")
    token_aud = claims.get("aud")
    if aud not in (token_aud if isinstance(token_aud, list) else [token_aud]):
        raise AccessDenied("wrong audience")
    if now >= float(claims.get("exp", 0)) or now + 60 < float(claims.get("nbf", 0)):
        raise AccessDenied("expired")
    if emails and (claims.get("email") or "").lower() not in {e.lower() for e in emails}:
        raise AccessDenied("email not allowed")
    return claims
