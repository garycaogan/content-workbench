"""Passkey (WebAuthn) login, sessions and the request guard.

Rules:
- The first passkey can only be registered with the one-time setup code, which
  exists only on this machine's private data directory.
- Further passkeys (another phone or laptop) require an existing session.
- Every API route except the auth endpoints requires a valid session.
- Every request must carry an allowed Host; state-changing requests must carry
  an allowed Origin. Cookies are HttpOnly and SameSite=Strict.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from . import access, db
from .config import Settings, clear_setup_code, ensure_setup_code

COOKIE = "cw_session"
USER_ID = b"gary-content-workbench"
CHALLENGE_TTL = 300
PUBLIC_API = {"/api/auth/state", "/api/auth/register/options", "/api/auth/register/verify",
              "/api/auth/login/options", "/api/auth/login/verify"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def has_credentials(rp_id: str | None = None) -> bool:
    """Whether a passkey exists, optionally for one address (rp_id).

    Passkeys only work on the address they were created for, so moving from
    localhost to the Tailscale address needs a new first registration there."""
    if rp_id is None:
        return db.row("SELECT 1 AS x FROM credentials LIMIT 1") is not None
    return db.row("SELECT 1 AS x FROM credentials WHERE rp_id=? LIMIT 1", (rp_id,)) is not None


def session_valid(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    if settings.access_login and getattr(request.state, "access_email", None):
        return True  # the Access token was verified for this very request in Guard
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    found = db.row("SELECT expires_at FROM sessions WHERE token_hash=?", (_hash(token),))
    return bool(found and found["expires_at"] > time.time())


def _new_session(response: Response, request: Request, settings: Settings) -> None:
    token = secrets.token_urlsafe(32)
    ttl = settings.session_days * 86400
    with db.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT INTO sessions(token_hash, created_at, expires_at, user_agent) VALUES (?,?,?,?)",
                     (_hash(token), time.time(), time.time() + ttl, request.headers.get("user-agent", "")[:200]))
    response.set_cookie(COOKIE, token, max_age=ttl, httponly=True, samesite="strict",
                        secure=settings.secure_cookies, path="/")


def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        with db.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))
    response.delete_cookie(COOKIE, path="/")


def _store_challenge(kind: str, challenge: bytes) -> str:
    cid = secrets.token_urlsafe(16)
    with db.tx() as conn:
        conn.execute("DELETE FROM challenges WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT INTO challenges(id, kind, challenge, expires_at) VALUES (?,?,?,?)",
                     (cid, kind, challenge, time.time() + CHALLENGE_TTL))
    return cid


def _take_challenge(cid: str, kind: str) -> bytes:
    with db.tx() as conn:
        found = conn.execute("SELECT challenge, expires_at FROM challenges WHERE id=? AND kind=?", (cid, kind)).fetchone()
        conn.execute("DELETE FROM challenges WHERE id=?", (cid,))
    if not found or found["expires_at"] < time.time():
        raise HTTPException(400, "验证已过期，请重试")
    return found["challenge"]


def registration_options(request: Request, settings: Settings, setup_code: str | None) -> dict:
    if has_credentials(settings.rp_id):
        if not session_valid(request):
            raise HTTPException(401, "添加新设备需要先在已登录的设备上操作")
    else:
        expected = ensure_setup_code()
        if not setup_code or not hmac.compare_digest(setup_code.strip(), expected):
            raise HTTPException(403, "初始化码不对")
    existing = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["id"]))
                for r in db.rows("SELECT id FROM credentials WHERE rp_id=?", (settings.rp_id,))]
    opts = generate_registration_options(
        rp_id=settings.rp_id, rp_name="内容工作台", user_name="gary", user_id=USER_ID,
        user_display_name=settings.author or "owner", exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED),
    )
    cid = _store_challenge("register", opts.challenge)
    return {"challenge_id": cid, "options": options_to_json(opts)}


def registration_verify(request: Request, response: Response, settings: Settings, body: dict) -> dict:
    first = not has_credentials(settings.rp_id)
    if first:
        expected = ensure_setup_code()
        if not hmac.compare_digest(str(body.get("setup_code", "")).strip(), expected):
            raise HTTPException(403, "初始化码不对")
    elif not session_valid(request):
        raise HTTPException(401, "需要先登录")
    challenge = _take_challenge(str(body.get("challenge_id", "")), "register")
    try:
        verified = verify_registration_response(
            credential=body["credential"], expected_challenge=challenge,
            expected_rp_id=settings.rp_id, expected_origin=settings.origins,
            require_user_verification=True)
    except Exception as exc:  # library raises several specific types
        raise HTTPException(400, f"passkey 校验没通过：{exc}") from None
    with db.tx() as conn:
        conn.execute("INSERT INTO credentials(id, public_key, sign_count, rp_id, label, created_at) VALUES (?,?,?,?,?,?)",
                     (bytes_to_base64url(verified.credential_id), verified.credential_public_key,
                      verified.sign_count, settings.rp_id, str(body.get("label", ""))[:60], time.time()))
    if first:
        clear_setup_code()
        _new_session(response, request, settings)
    return {"ok": True}


def login_options(settings: Settings) -> dict:
    opts = generate_authentication_options(rp_id=settings.rp_id,
                                           user_verification=UserVerificationRequirement.REQUIRED)
    cid = _store_challenge("login", opts.challenge)
    return {"challenge_id": cid, "options": options_to_json(opts)}


def login_verify(request: Request, response: Response, settings: Settings, body: dict) -> dict:
    challenge = _take_challenge(str(body.get("challenge_id", "")), "login")
    credential = body.get("credential") or {}
    cred_id = str(credential.get("id", ""))
    stored = db.row("SELECT * FROM credentials WHERE id=? AND rp_id=?", (cred_id, settings.rp_id))
    if not stored:
        raise HTTPException(401, "这个 passkey 不认识")
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=settings.rp_id,
            expected_origin=settings.origins, credential_public_key=stored["public_key"],
            credential_current_sign_count=stored["sign_count"], require_user_verification=True)
    except Exception as exc:
        raise HTTPException(401, f"登录校验没通过：{exc}") from None
    with db.tx() as conn:
        conn.execute("UPDATE credentials SET sign_count=?, last_used_at=? WHERE id=?",
                     (verified.new_sign_count, time.time(), cred_id))
    _new_session(response, request, settings)
    return {"ok": True}


class Guard(BaseHTTPMiddleware):
    """Host/Origin allowlist plus session requirement for the API."""

    async def dispatch(self, request: Request, call_next):
        settings: Settings = request.app.state.settings
        host = request.headers.get("host", "")
        if host not in settings.allowed_hosts:
            return JSONResponse({"detail": "host not allowed"}, status_code=421)
        if settings.access_team:
            token = request.headers.get("cf-access-jwt-assertion", "")
            try:
                claims = await asyncio.to_thread(access.verify, token, settings.access_team, settings.access_aud, settings.access_emails)
                request.state.access_email = (claims or {}).get("email") or "access"
            except Exception as exc:
                print(f"access check failed for {request.url.path}: {exc}", flush=True)
                return JSONResponse({"detail": "access check failed"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin not in settings.origins:
                return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_API and not path.startswith("/api/publish/x/callback"):
            if not session_valid(request):
                return JSONResponse({"detail": "需要登录"}, status_code=401)
        response = await call_next(request)
        if "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
