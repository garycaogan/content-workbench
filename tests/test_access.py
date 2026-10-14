import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from content_workbench import access
from content_workbench.app import create_app
from content_workbench.config import Settings

TEAM, AUD, HOST = "gary", "aud-123", "write.example.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def token(**overrides) -> str:
    now = time.time()
    claims = {"iss": access.issuer(TEAM), "aud": [AUD], "email": "gary@example.com", "exp": now + 600, "nbf": now - 5}
    claims.update(overrides)
    head = _b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    body = _b64(json.dumps(claims).encode())
    sig = KEY.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(sig)}"


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    monkeypatch.setattr(access, "fetch_keys", lambda team: {"k1": KEY.public_key()})


def client(login_mode="passkey"):
    s = Settings(rp_id=HOST, origins=[f"https://{HOST}"], allowed_hosts=[HOST],
                 access_team=TEAM, access_aud=AUD, access_emails=["gary@example.com"], login_mode=login_mode)
    return TestClient(create_app(s, start_jobs=False), base_url=f"https://{HOST}")


def test_request_without_access_token_is_refused():
    assert client().get("/api/auth/state").status_code == 403
    assert client().get("/").status_code == 403


def test_valid_access_token_reaches_the_app():
    r = client().get("/api/auth/state", headers={"Cf-Access-Jwt-Assertion": token()})
    assert r.status_code == 200


@pytest.mark.parametrize("bad", [
    {"aud": ["someone-else"]},
    {"exp": time.time() - 1},
    {"iss": "https://other.cloudflareaccess.com"},
    {"email": "stranger@example.com"},
])
def test_wrong_claims_are_refused(bad):
    r = client().get("/api/auth/state", headers={"Cf-Access-Jwt-Assertion": token(**bad)})
    assert r.status_code == 403


def test_tampered_token_is_refused():
    head, body, sig = token().split(".")
    forged = _b64(json.dumps({"iss": access.issuer(TEAM), "aud": [AUD], "email": "gary@example.com",
                              "exp": time.time() + 600}).encode())
    r = client().get("/api/auth/state", headers={"Cf-Access-Jwt-Assertion": f"{head}.{forged}.{sig}"})
    assert r.status_code == 403


def test_email_code_login_needs_no_passkey():
    c = client("access")
    h = {"Cf-Access-Jwt-Assertion": token()}
    state = c.get("/api/auth/state", headers=h).json()
    assert state["logged_in"] is True and state["login_mode"] == "access"
    assert c.get("/api/materials", headers=h).status_code == 200
    assert c.get("/api/materials").status_code == 403  # still nothing without the Access token
    assert c.get("/api/materials", headers={"Cf-Access-Jwt-Assertion": token(email="stranger@example.com")}).status_code == 403


def test_passkey_mode_still_requires_a_session():
    c = client("passkey")
    assert c.get("/api/materials", headers={"Cf-Access-Jwt-Assertion": token()}).status_code == 401


def test_access_login_is_ignored_without_access_configured():
    s = Settings(login_mode="access")
    assert s.access_login is False
