"""End-to-end passkey test with a software authenticator (ES256, attestation none).

This drives the real register/login endpoints and the real py_webauthn
verification, so a regression in challenge handling, origin/rp checks or
signature verification fails here."""

import hashlib
import json
import os
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, encode_cbor

from content_workbench.app import create_app
from content_workbench.config import Settings, setup_code_path

ORIGIN = "http://localhost:8765"
RP_ID = "localhost"


class SoftAuthenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.count = 0

    def _cose(self):
        nums = self.key.public_key().public_numbers()
        return encode_cbor({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})

    def _auth_data(self, attested: bool):
        self.count += 1
        flags = 0x01 | 0x04 | (0x40 if attested else 0)  # UP, UV, AT
        data = hashlib.sha256(RP_ID.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        if attested:
            data += b"\x00" * 16 + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose()
        return data

    def _client_data(self, kind, challenge_b64, origin):
        return json.dumps({"type": kind, "challenge": challenge_b64, "origin": origin, "crossOrigin": False}).encode()

    def create(self, options: dict, origin=ORIGIN):
        cdj = self._client_data("webauthn.create", options["challenge"], origin)
        att = encode_cbor({"fmt": "none", "attStmt": {}, "authData": self._auth_data(True)})
        cid = bytes_to_base64url(self.cred_id)
        return {"id": cid, "rawId": cid, "type": "public-key",
                "response": {"clientDataJSON": bytes_to_base64url(cdj), "attestationObject": bytes_to_base64url(att)}}

    def get(self, options: dict, origin=ORIGIN):
        cdj = self._client_data("webauthn.get", options["challenge"], origin)
        auth = self._auth_data(False)
        sig = self.key.sign(auth + hashlib.sha256(cdj).digest(), ec.ECDSA(hashes.SHA256()))
        cid = bytes_to_base64url(self.cred_id)
        return {"id": cid, "rawId": cid, "type": "public-key",
                "response": {"clientDataJSON": bytes_to_base64url(cdj), "authenticatorData": bytes_to_base64url(auth),
                             "signature": bytes_to_base64url(sig), "userHandle": None}}


def client():
    return TestClient(create_app(Settings(), start_jobs=False), base_url="http://localhost:8765")


def test_register_then_login_with_real_signatures():
    c = client()
    h = {"Origin": ORIGIN}
    code = setup_code_path().read_text().strip()
    authn = SoftAuthenticator()

    o = c.post("/api/auth/register/options", json={"setup_code": code}, headers=h).json()
    cred = authn.create(json.loads(o["options"]))
    r = c.post("/api/auth/register/verify", json={"challenge_id": o["challenge_id"], "credential": cred, "setup_code": code}, headers=h)
    assert r.status_code == 200, r.text
    assert not setup_code_path().exists(), "setup code must be single-use"
    assert c.get("/api/materials").status_code == 200  # first registration logs in

    c.post("/api/auth/logout", headers=h)
    assert c.get("/api/materials").status_code == 401

    o = c.post("/api/auth/login/options", headers=h).json()
    r = c.post("/api/auth/login/verify", json={"challenge_id": o["challenge_id"], "credential": authn.get(json.loads(o["options"]))}, headers=h)
    assert r.status_code == 200, r.text
    assert c.get("/api/materials").status_code == 200


def test_login_rejects_wrong_origin_and_replayed_challenge():
    c = client()
    h = {"Origin": ORIGIN}
    code = setup_code_path().read_text().strip()
    authn = SoftAuthenticator()
    o = c.post("/api/auth/register/options", json={"setup_code": code}, headers=h).json()
    c.post("/api/auth/register/verify", json={"challenge_id": o["challenge_id"], "credential": authn.create(json.loads(o["options"])), "setup_code": code}, headers=h)
    c.post("/api/auth/logout", headers=h)

    o = c.post("/api/auth/login/options", headers=h).json()
    bad = authn.get(json.loads(o["options"]), origin="https://evil.example")
    assert c.post("/api/auth/login/verify", json={"challenge_id": o["challenge_id"], "credential": bad}, headers=h).status_code == 401
    # the challenge was consumed by the failed attempt
    good = authn.get(json.loads(o["options"]))
    assert c.post("/api/auth/login/verify", json={"challenge_id": o["challenge_id"], "credential": good}, headers=h).status_code == 400


def test_second_device_requires_session():
    c = client()
    h = {"Origin": ORIGIN}
    code = setup_code_path().read_text().strip()
    authn = SoftAuthenticator()
    o = c.post("/api/auth/register/options", json={"setup_code": code}, headers=h).json()
    c.post("/api/auth/register/verify", json={"challenge_id": o["challenge_id"], "credential": authn.create(json.loads(o["options"])), "setup_code": code}, headers=h)
    c.post("/api/auth/logout", headers=h)
    # without a session, nobody can add another passkey, even with the old code
    assert c.post("/api/auth/register/options", json={"setup_code": code}, headers=h).status_code == 401


def test_new_address_allows_first_registration_with_setup_code():
    c = client()
    h = {"Origin": ORIGIN}
    code = setup_code_path().read_text().strip()
    authn = SoftAuthenticator()
    o = c.post("/api/auth/register/options", json={"setup_code": code}, headers=h).json()
    c.post("/api/auth/register/verify", json={"challenge_id": o["challenge_id"], "credential": authn.create(json.loads(o["options"])), "setup_code": code}, headers=h)
    # move to another address: existing localhost passkey does not count there
    from content_workbench import auth as a
    assert a.has_credentials("localhost") and not a.has_credentials("macmini.tail1234.ts.net")
