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


def save(c, did, fields, headers):
    """Save the way the editor does: against the version it last loaded."""
    rev = c.get(f"/api/drafts/{did}").json()["rev"]
    return c.put(f"/api/drafts/{did}", json={**fields, "base": rev}, headers=headers)


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
    save(c, did, {"body": "第一条\n---\n第二条"}, headers=h)
    assert c.get(f"/api/drafts/{did}").json()["body"] == "第一条\n---\n第二条"
    prev = c.post("/api/preview", json={"platform": "x", "body": "第一条\n---\n第二条"}, headers=h).json()
    assert [t["weighted"] for t in prev["thread"]] == [6, 6]


def test_stale_editor_cannot_overwrite_a_change_made_elsewhere():
    c = client()
    login(c)
    h = {"Origin": ORIGIN}
    did = c.post("/api/drafts", json={"platform": "wechat", "title": "旧标题", "body": "旧正文"}, headers=h).json()["id"]
    opened = c.get(f"/api/drafts/{did}").json()["rev"]  # the editor page loads the draft
    with db.tx() as conn:  # an AI revision written straight into the database while the page stays open
        conn.execute("UPDATE drafts SET body='AI 改过的正文', notes='AI 的备注', context='新的背景' WHERE id=?", (did,))
    now = c.get(f"/api/drafts/{did}/rev").json()["rev"]
    assert [k for k in now if now[k] != opened[k]] == ["body", "notes", "context"]  # context changes don't move updated_at

    # the old page saving its stale body and notes is refused, and nothing is written
    r = c.put(f"/api/drafts/{did}", json={"title": "新标题", "body": "旧正文，改了一点", "notes": "", "base": opened}, headers=h)
    assert r.status_code == 409 and "正文、备注" in r.json()["detail"]
    d = c.get(f"/api/drafts/{did}").json()
    assert (d["title"], d["body"], d["notes"]) == ("旧标题", "AI 改过的正文", "AI 的备注")

    # sending only what it changed goes through and leaves the newer body alone
    r = c.put(f"/api/drafts/{did}", json={"title": "新标题", "base": {"title": opened["title"]}}, headers=h)
    assert r.status_code == 200 and r.json()["rev"]["body"] == now["body"]
    d = c.get(f"/api/drafts/{did}").json()
    assert (d["title"], d["body"], d["notes"]) == ("新标题", "AI 改过的正文", "AI 的备注")
    # a field sent unchanged isn't a clash, and a save from the current version is a normal save
    assert c.put(f"/api/drafts/{did}", json={"body": "AI 改过的正文", "base": opened}, headers=h).status_code == 200
    assert c.put(f"/api/drafts/{did}", json={"body": "我接着改", "base": d["rev"]}, headers=h).status_code == 200

    # a page opened before this check existed sends no base at all
    assert c.put(f"/api/drafts/{did}", json={"title": "x", "body": "旧正文"}, headers=h).status_code == 428
    assert c.put(f"/api/drafts/{did}", json={"cover_crop": {"square_x": 0.2}}, headers=h).status_code == 200

    # what the page couldn't save is kept in the history instead of lost
    vid = c.post(f"/api/drafts/{did}/versions", json={"origin": "unsaved", "title": "新标题", "body": "旧正文，改了一点"},
                 headers=h).json()["id"]
    first = c.get(f"/api/drafts/{did}/versions").json()[0]
    assert (first["id"], first["origin"], first["body"]) == (vid, "unsaved", "旧正文，改了一点")
    assert c.post(f"/api/drafts/{did}/versions", json={"origin": "edit", "body": "x"}, headers=h).status_code == 400


def test_publish_requires_confirm():
    c = client()
    login(c)
    h = {"Origin": ORIGIN}
    did = c.post("/api/drafts", json={"platform": "x", "title": "t", "body": "a"}, headers=h).json()["id"]
    assert c.post(f"/api/drafts/{did}/publish", json={}, headers=h).status_code == 400
    r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True}, headers=h)
    assert r.status_code == 400 and "X 还没连接" in r.json()["detail"]
