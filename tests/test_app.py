from fastapi.testclient import TestClient

from content_workbench import auth, db
from content_workbench.app import create_app
from content_workbench.config import Settings, setup_code_path

ORIGIN = "http://localhost:8765"
HOST = "localhost:8765"


def client(**settings):
    return TestClient(create_app(Settings(**settings), start_jobs=False), base_url=f"http://{HOST}")


def login(c):
    import secrets, time
    token = secrets.token_urlsafe(16)
    with db.tx() as conn:
        conn.execute("INSERT INTO sessions(token_hash, created_at, expires_at) VALUES (?,?,?)",
                     (auth._hash(token), time.time(), time.time() + 3600))
    c.cookies.set(auth.COOKIE, token)


def test_unknown_host_rejected():
    c = TestClient(create_app(Settings(), start_jobs=False), base_url="http://evil.example")
    assert c.get("/api/auth/state").status_code == 421


def test_api_requires_session():
    c = client()
    assert c.get("/api/materials").status_code == 401
    assert c.get("/api/auth/state").json() == {"registered": False, "logged_in": False, "login_mode": "passkey"}


def test_post_requires_allowed_origin():
    c = client()
    login(c)
    assert c.post("/api/materials", json={"body": "x", "extract": False}).status_code == 403
    r = c.post("/api/materials", json={"body": "今天测试", "extract": False}, headers={"Origin": ORIGIN})
    assert r.status_code == 200


def test_first_registration_needs_setup_code():
    c = client()
    assert setup_code_path().exists()
    r = c.post("/api/auth/register/options", json={"setup_code": "wrong"}, headers={"Origin": ORIGIN})
    assert r.status_code == 403
    code = setup_code_path().read_text().strip()
    r = c.post("/api/auth/register/options", json={"setup_code": code}, headers={"Origin": ORIGIN})
    assert r.status_code == 200 and "challenge_id" in r.json()


def test_draft_roundtrip_and_preview():
    c = client()
    login(c)
    h = {"Origin": ORIGIN}
    did = c.post("/api/drafts", json={"platform": "x", "title": "t", "body": "a\n---\nb"}, headers=h).json()["id"]
    c.put(f"/api/drafts/{did}", json={"body": "第一条\n---\n第二条"}, headers=h)
    assert c.get(f"/api/drafts/{did}").json()["body"] == "第一条\n---\n第二条"
    prev = c.post("/api/preview", json={"platform": "x", "body": "第一条\n---\n第二条"}, headers=h).json()
    assert [t["weighted"] for t in prev["thread"]] == [6, 6]


def test_publish_requires_confirm():
    c = client()
    login(c)
    h = {"Origin": ORIGIN}
    did = c.post("/api/drafts", json={"platform": "x", "title": "t", "body": "a"}, headers=h).json()["id"]
    assert c.post(f"/api/drafts/{did}/publish", json={}, headers=h).status_code == 400
    r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True}, headers=h)
    assert r.status_code == 400 and "X 还没连接" in r.json()["detail"]
