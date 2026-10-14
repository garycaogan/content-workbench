"""FastAPI application: API + static frontend."""

from __future__ import annotations

import json
import re
import secrets
import shutil
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from . import auth, comments, db, images, jobs, learning, llm, prompts, publish, sources, xarticle, xhs, zsxq
from .config import SecretStore, Settings, data_dir, ensure_setup_code
from .textutil import normalize_emphasis, thread_report, wechat_html

WX_INLINE_MAX = 1024 * 1024 - 4096      # media/uploadimg: jpg/png under 1MB
WX_COVER_MAX = 9 * 1024 * 1024          # permanent image material: 10MB
X_IMAGE_MAX = 5 * 1024 * 1024 - 4096    # X pictures: 5MB
DRAFT_FIELDS = ("title", "body", "notes", "summary", "source_url")

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
SECRET_KEYS = ("x_client_id", "x_client_secret", "wx_appid", "wx_secret")


def create_app(settings: Settings | None = None, start_jobs: bool = True) -> FastAPI:
    settings = settings or Settings.load()
    app = FastAPI(title="内容工作台", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.add_middleware(auth.Guard)
    store = SecretStore()
    db.get()
    if not auth.has_credentials(settings.rp_id):
        ensure_setup_code()
    if start_jobs:
        jobs.start(settings)

    # ------------------------------------------------------------ auth
    @app.get("/api/auth/state")
    def auth_state(request: Request):
        return {"registered": auth.has_credentials(settings.rp_id) or settings.access_login,
                "logged_in": auth.session_valid(request), "login_mode": "access" if settings.access_login else "passkey"}

    @app.post("/api/auth/register/options")
    def reg_options(request: Request, body: dict = Body(default={})):
        return auth.registration_options(request, settings, body.get("setup_code"))

    @app.post("/api/auth/register/verify")
    def reg_verify(request: Request, response: Response, body: dict = Body(...)):
        return auth.registration_verify(request, response, settings, body)

    @app.post("/api/auth/login/options")
    def login_options():
        return auth.login_options(settings)

    @app.post("/api/auth/login/verify")
    def login_verify(request: Request, response: Response, body: dict = Body(...)):
        return auth.login_verify(request, response, settings, body)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        auth.logout(request, response)
        return {"ok": True}

    @app.get("/api/auth/credentials")
    def credentials():
        return db.rows("SELECT id, label, rp_id, created_at, last_used_at FROM credentials ORDER BY created_at")

    # ------------------------------------------------------------ status
    @app.get("/api/status")
    def status():
        return {
            "llm": llm.status(),
            "transcription": sources.transcription_available(),
            "x_connected": bool(store.get("x_tokens")),
            "x_media": "media.write" in publish.x_scopes(store),
            "zsxq_connected": bool(store.get("zsxq_token")),
            "zsxq_group": settings.zsxq_group_name or settings.zsxq_group_id,
            "case_search": prompts.case_search_on(settings),
            "author": settings.author,
            "wechat_configured": all(store.present("wx_appid", "wx_secret").values()),
            "wechat_cover": bool(store.get("wx_default_cover")),
            "counts": {
                "materials": db.row("SELECT COUNT(*) n FROM materials")["n"],
                "ideas_new": db.row("SELECT COUNT(*) n FROM ideas WHERE status='new'")["n"],
                "drafts": db.row("SELECT COUNT(*) n FROM drafts WHERE status!='published'")["n"],
            },
            "jobs_active": db.row("SELECT COUNT(*) n FROM jobs WHERE state IN ('queued','running')")["n"],
        }

    @app.get("/api/jobs")
    def list_jobs():
        return db.rows("SELECT id, kind, payload, state, error, created_at, updated_at FROM jobs ORDER BY id DESC LIMIT 30")

    # ------------------------------------------------------------ materials
    @app.get("/api/materials")
    def list_materials():
        return db.rows("""SELECT m.id, m.source, m.source_ref, m.title, substr(m.body,1,240) AS preview, length(m.body) AS length,
                          m.status, m.error, m.created_at,
                          (SELECT COUNT(*) FROM ideas i WHERE i.material_id=m.id) AS idea_count
                          FROM materials m ORDER BY m.created_at DESC LIMIT 300""")

    @app.get("/api/materials/{mid}")
    def get_material(mid: int):
        found = db.row("SELECT * FROM materials WHERE id=?", (mid,))
        if not found:
            raise HTTPException(404)
        return found

    @app.post("/api/materials")
    def add_note(body: dict = Body(...)):
        text = str(body.get("body", "")).strip()
        if not text:
            raise HTTPException(400, "内容是空的")
        title = str(body.get("title") or text.splitlines()[0][:40])
        with db.tx() as conn:
            cur = conn.execute("INSERT INTO materials(source, source_ref, title, body, created_at) VALUES ('note', ?, ?, ?, ?)",
                               (secrets.token_hex(6), title, text, db.now()))
            mid = cur.lastrowid
        if body.get("extract", True):
            jobs.enqueue("extract", {"material_id": mid})
        return {"id": mid}

    @app.put("/api/materials/{mid}")
    def update_material(mid: int, body: dict = Body(...)):
        with db.tx() as conn:
            conn.execute("UPDATE materials SET title=COALESCE(?, title), body=COALESCE(?, body) WHERE id=?",
                         (body.get("title"), body.get("body"), mid))
        return {"ok": True}

    @app.delete("/api/materials/{mid}")
    def delete_material(mid: int):
        with db.tx() as conn:
            conn.execute("DELETE FROM materials WHERE id=?", (mid,))
        return {"ok": True}

    @app.post("/api/materials/audio")
    async def add_audio(file: UploadFile = File(...), title: str = Form(""), extract: str = Form("1")):
        if not sources.transcription_available():
            raise HTTPException(400, "本机还没装语音转写组件，先在设置页看说明")
        suffix = Path(file.filename or "audio.m4a").suffix.lower() or ".m4a"
        if suffix not in {".m4a", ".mp3", ".wav", ".webm", ".ogg", ".aac", ".mp4", ".mov", ".flac"}:
            raise HTTPException(400, "不支持这个音频格式")
        target = jobs.uploads_dir() / f"{secrets.token_hex(8)}{suffix}"
        with target.open("wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        target.chmod(0o600)
        name = title.strip() or (file.filename or "录音")
        with db.tx() as conn:
            cur = conn.execute("INSERT INTO materials(source, source_ref, title, body, status, created_at) VALUES ('audio', ?, ?, '', 'transcribing', ?)",
                               (target.name, name, db.now()))
            mid = cur.lastrowid
        jobs.enqueue("transcribe", {"material_id": mid, "path": str(target)})
        if extract == "1":
            jobs.enqueue("extract", {"material_id": mid})
        return {"id": mid}

    @app.post("/api/materials/feishu-doc")
    def add_feishu_doc(body: dict = Body(...)):
        url = str(body.get("url", "")).strip()
        if not re.match(r"^https://[\w.-]+\.(feishu\.cn|larksuite\.com)/(docx|wiki|docs)/\w+", url):
            raise HTTPException(400, "这不像一个飞书文档链接")
        try:
            title, content = sources.fetch_doc(url)
        except sources.SourceError as exc:
            raise HTTPException(400, str(exc))
        return _insert_material("feishu_doc", url, title, content, body.get("extract", True))

    @app.get("/api/feishu/minutes")
    def feishu_minutes(start: str, end: str):
        try:
            items = sources.list_minutes(start, end)
        except sources.SourceError as exc:
            raise HTTPException(400, str(exc))
        imported = {r["source_ref"] for r in db.rows("SELECT source_ref FROM materials WHERE source='feishu_minutes'")}
        for it in items:
            it["imported"] = it["token"] in imported
        return items

    @app.post("/api/materials/feishu-minutes")
    def add_minutes(body: dict = Body(...)):
        token = str(body.get("token", ""))
        if not re.fullmatch(r"[a-z0-9]{10,64}", token):
            raise HTTPException(400, "妙记 token 不对")
        try:
            text = sources.fetch_minute_transcript(token)
        except sources.SourceError as exc:
            raise HTTPException(400, str(exc))
        return _insert_material("feishu_minutes", token, str(body.get("title") or "飞书妙记"), text, body.get("extract", True))

    def _insert_material(source: str, ref: str, title: str, text: str, extract) -> dict:
        existing = db.row("SELECT id FROM materials WHERE source=? AND source_ref=?", (source, ref))
        if existing:
            return {"id": existing["id"], "duplicate": True}
        with db.tx() as conn:
            cur = conn.execute("INSERT INTO materials(source, source_ref, title, body, created_at) VALUES (?,?,?,?,?)",
                               (source, ref, title[:200], text, db.now()))
            mid = cur.lastrowid
        if extract:
            jobs.enqueue("extract", {"material_id": mid})
        return {"id": mid}

    # ------------------------------------------------------------ Feishu library
    @app.get("/api/feishu/library")
    def feishu_library():
        state = jobs.library_state()
        if not state.get("items") and not state.get("refreshing"):
            jobs.enqueue("refresh_library", {})
            state["refreshing"] = True
        overrides = _triage()
        published = sources.published_titles(settings.kb_root)
        imported = {r["source_ref"] for r in db.rows("SELECT source_ref FROM materials WHERE source IN ('feishu_doc','feishu_table')")}
        queued = {json.loads(r["payload"]).get("token") for r in db.rows("SELECT payload FROM jobs WHERE kind='import_feishu' AND state IN ('queued','running')")}
        failed = state.get("failed", {})
        items = []
        for it in state.get("items") or []:
            cat = overrides.get(it["token"]) or sources.classify_doc(it["title"])
            pub = sources.looks_published(it["title"], published)
            items.append({**it, "category": cat, "published": pub, "imported": it["token"] in imported,
                          "queued": it["token"] in queued, "failed": failed.get(it["token"]),
                          "suggested": cat == "own" and not pub})
        return {"items": items, "updated_at": state.get("updated_at"), "refreshing": bool(state.get("refreshing"))}

    @app.post("/api/feishu/library/refresh")
    def feishu_library_refresh():
        jobs.enqueue("refresh_library", {})
        return {"ok": True}

    @app.post("/api/feishu/import")
    def feishu_import(body: dict = Body(...)):
        known = {it["token"]: it for it in (jobs.library_state().get("items") or [])}
        count = 0
        for token in body.get("tokens") or []:
            it = known.get(str(token))
            if not it or it["type"] not in ("docx", "doc", "wiki", "sheet", "bitable"):
                continue
            jobs.enqueue("import_feishu", {"token": it["token"], "url": it["url"], "title": it["title"],
                                           "created": it["created"], "type": it["type"]})
            count += 1
        return {"queued": count}

    def _triage() -> dict:
        path = data_dir() / "feishu_triage.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    @app.post("/api/materials/{mid}/extract")
    def extract(mid: int):
        if not db.row("SELECT 1 x FROM materials WHERE id=?", (mid,)):
            raise HTTPException(404)
        return {"job": jobs.enqueue("extract", {"material_id": mid})}

    # ------------------------------------------------------------ ideas
    @app.get("/api/ideas")
    def list_ideas(status: str = "new"):
        found = db.rows("""SELECT i.*, m.title AS material_title FROM ideas i LEFT JOIN materials m ON m.id=i.material_id
                           WHERE (?='all' OR i.status=?) ORDER BY i.starred DESC, i.created_at DESC LIMIT 2000""", (status, status))
        for r in found:
            r["platforms"] = db.loads(r["platforms"], [])
            r["quotes"] = db.loads(r["quotes"], [])
        return found

    @app.post("/api/ideas")
    def add_idea(body: dict = Body(...)):
        title = str(body.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "选题不能为空")
        with db.tx() as conn:
            cur = conn.execute("INSERT INTO ideas(material_id, title, angle, platforms, quotes, notes, created_at) VALUES (?,?,?,?,?,?,?)",
                               (body.get("material_id"), title, body.get("angle", ""), db.dumps(body.get("platforms") or []),
                                db.dumps([]), "", db.now()))
        return {"id": cur.lastrowid}

    @app.patch("/api/ideas/{iid}")
    def update_idea(iid: int, body: dict = Body(...)):
        with db.tx() as conn:
            if "starred" in body:
                conn.execute("UPDATE ideas SET starred=? WHERE id=?", (1 if body["starred"] else 0, iid))
            if body.get("status") in ("new", "drafted", "dropped"):
                conn.execute("UPDATE ideas SET status=? WHERE id=?", (body["status"], iid))
            for field in ("title", "angle"):
                if field in body:
                    conn.execute(f"UPDATE ideas SET {field}=? WHERE id=?", (str(body[field]), iid))
        return {"ok": True}

    @app.post("/api/ideas/{iid}/draft")
    def draft_from_idea(iid: int, body: dict = Body(...)):
        platform = body.get("platform")
        if platform not in ("wechat", "x"):
            raise HTTPException(400, "平台只能是 wechat 或 x")
        idea = db.row("SELECT * FROM ideas WHERE id=?", (iid,))
        if not idea:
            raise HTTPException(404)
        with db.tx() as conn:
            cur = conn.execute("INSERT INTO drafts(idea_id, platform, title, status, created_at, updated_at) VALUES (?,?,?, 'generating', ?, ?)",
                               (iid, platform, idea["title"], db.now(), db.now()))
            did = cur.lastrowid
        jobs.enqueue("draft", {"draft_id": did})
        return {"id": did}

    # ------------------------------------------------------------ drafts
    @app.get("/api/drafts")
    def list_drafts():
        return db.rows("""SELECT id, idea_id, platform, title, substr(body,1,160) AS preview, status, error, created_at, updated_at
                          FROM drafts ORDER BY updated_at DESC LIMIT 300""")

    @app.post("/api/drafts")
    def new_draft(body: dict = Body(...)):
        platform = body.get("platform")
        if platform not in ("wechat", "x"):
            raise HTTPException(400, "平台只能是 wechat 或 x")
        with db.tx() as conn:
            cur = conn.execute("INSERT INTO drafts(platform, title, body, created_at, updated_at) VALUES (?,?,?,?,?)",
                               (platform, str(body.get("title", "")), str(body.get("body", "")), db.now(), db.now()))
        return {"id": cur.lastrowid}

    @app.get("/api/drafts/{did}")
    def get_draft(did: int):
        found = db.row("SELECT * FROM drafts WHERE id=?", (did,))
        if not found:
            raise HTTPException(404)
        found["publications"] = db.rows("SELECT * FROM publications WHERE draft_id=? ORDER BY id DESC", (did,))
        found["images"] = images.for_draft(did)
        found["cover_crop"] = db.loads(found.get("cover_crop"), {})
        found["xhs"] = xhs.load(found)
        return found

    @app.put("/api/drafts/{did}")
    def save_draft(did: int, body: dict = Body(...)):
        current = db.row("SELECT * FROM drafts WHERE id=?", (did,))
        if not current:
            raise HTTPException(404)
        values = {k: str(body[k]) if body.get(k) is not None else (current[k] or "") for k in DRAFT_FIELDS}
        if current["platform"] != "x":
            values["body"] = normalize_emphasis(values["body"])
        values["body"] = comments.strip_lifted(did, values["body"])
        title, text = values["title"], values["body"]
        source_url = values["source_url"].strip()
        if source_url and not re.match(r"^https?://\S+$", source_url):
            raise HTTPException(400, "阅读原文要填一个完整的网址")
        values["source_url"] = source_url
        covers = {}
        for key in ("cover_wechat", "cover_x"):
            if key in body:
                cid = body[key]
                if cid is not None and not db.row("SELECT 1 x FROM images WHERE id=? AND draft_id=?", (int(cid), did)):
                    raise HTTPException(400, "这张图不属于这篇稿子")
                covers[key] = int(cid) if cid is not None else None
        crop = body.get("cover_crop", None)
        with db.tx() as conn:
            conn.execute("UPDATE drafts SET title=?, body=?, notes=?, summary=?, source_url=?, updated_at=? WHERE id=?",
                         (title, text, values["notes"], values["summary"], source_url, db.now(), did))
            for key, cid in covers.items():
                conn.execute(f"UPDATE drafts SET {key}=? WHERE id=?", (cid, did))
            if isinstance(crop, dict):
                x = min(max(float(crop.get("square_x", 0)), 0.0), 1.0)
                conn.execute("UPDATE drafts SET cover_crop=? WHERE id=?", (db.dumps({"square_x": x}), did))
            last = conn.execute("SELECT created_at, body FROM draft_versions WHERE draft_id=? ORDER BY id DESC LIMIT 1", (did,)).fetchone()
            if (not last or db.now() - last["created_at"] > 600) and text != (last["body"] if last else None):
                conn.execute("INSERT INTO draft_versions(draft_id, title, body, created_at, origin) VALUES (?,?,?,?, 'edit')",
                             (did, title, text, db.now()))
        return {"ok": True, "updated_at": db.now()}

    @app.delete("/api/drafts/{did}")
    def delete_draft(did: int):
        for im in images.for_draft(did):
            images.delete(im["id"])
        with db.tx() as conn:
            conn.execute("DELETE FROM drafts WHERE id=?", (did,))
        return {"ok": True}

    @app.get("/api/drafts/{did}/versions")
    def versions(did: int):
        return db.rows("SELECT id, title, body, created_at, origin FROM draft_versions WHERE draft_id=? ORDER BY id DESC", (did,))

    # ------------------------------------------------------------ Feishu archive and cases
    @app.post("/api/drafts/{did}/feishu")
    def archive_now(did: int, body: dict = Body(default={})):
        if not db.row("SELECT 1 x FROM drafts WHERE id=?", (did,)):
            raise HTTPException(404)
        force = body.get("force") is True
        if force:
            with db.tx() as conn:
                conn.execute("UPDATE drafts SET feishu_state=NULL, feishu_error=NULL WHERE id=? AND feishu_state='conflict'", (did,))
        return {"job": jobs.enqueue_archive(did, force=force)}

    @app.get("/api/drafts/{did}/comments")
    def list_comments(did: int):
        return comments.threads(did)

    @app.post("/api/drafts/{did}/comments")
    def add_comment(did: int, body: dict = Body(...)):
        text = str(body.get("body", "")).strip()
        if not text:
            raise HTTPException(400, "批注是空的")
        if not db.row("SELECT 1 x FROM drafts WHERE id=?", (did,)):
            raise HTTPException(404)
        quote = str(body.get("quote") or "").strip()[:500] or None
        cid = comments.add(did, "gary", text[:4000], quote=quote, prefix=str(body.get("prefix") or "")[:60] or None,
                           suffix=str(body.get("suffix") or "")[:60] or None)
        return {"id": cid}

    @app.post("/api/comments/{cid}/replies")
    def reply_comment(cid: int, body: dict = Body(...)):
        root = db.row("SELECT * FROM comments WHERE id=? AND parent_id IS NULL", (cid,))
        text = str(body.get("body", "")).strip()
        if not root:
            raise HTTPException(404)
        if not text:
            raise HTTPException(400, "回复是空的")
        return {"id": comments.add(root["draft_id"], "gary", text[:4000], parent_id=cid)}

    @app.patch("/api/comments/{cid}")
    def update_comment(cid: int, body: dict = Body(...)):
        if body.get("status") not in ("open", "resolved"):
            raise HTTPException(400, "状态只能是 open 或 resolved")
        with db.tx() as conn:
            conn.execute("UPDATE comments SET status=?, resolved_at=? WHERE id=? AND parent_id IS NULL",
                         (body["status"], db.now() if body["status"] == "resolved" else None, cid))
        return {"ok": True}

    @app.delete("/api/comments/{cid}")
    def delete_comment(cid: int):
        found = db.row("SELECT * FROM comments WHERE id=?", (cid,))
        if not found:
            raise HTTPException(404)
        if found["author"] != "gary" and found["parent_id"] is not None:
            raise HTTPException(400, "AI 的回复不能单独删，可以把整条批注标成已解决")
        with db.tx() as conn:
            conn.execute("DELETE FROM comments WHERE id=?", (cid,))
        return {"ok": True}

    @app.post("/api/drafts/{did}/revise")
    def revise_from_comments(did: int):
        draft = db.row("SELECT status FROM drafts WHERE id=?", (did,))
        if not draft:
            raise HTTPException(404)
        if draft["status"] in ("generating", "revising"):
            raise HTTPException(409, "AI 正在处理这篇稿子，等它改完再发")
        todo = comments.pending(did)
        if not todo:
            raise HTTPException(400, "没有需要 AI 处理的批注：写一条批注，或者回复 AI 的批注")
        with db.tx() as conn:
            conn.execute("UPDATE drafts SET status='revising', error=NULL WHERE id=?", (did,))
        jobs.enqueue("revise", {"draft_id": did})
        return {"threads": len(todo)}

    @app.get("/api/drafts/{did}/diff")
    def draft_diff(did: int, a: int, b: int | None = None):
        old = db.row("SELECT body FROM draft_versions WHERE id=? AND draft_id=?", (a, did))
        new = db.row("SELECT body FROM draft_versions WHERE id=? AND draft_id=?", (b, did)) if b else \
            db.row("SELECT body FROM drafts WHERE id=?", (did,))
        if not old or not new:
            raise HTTPException(404)
        return comments.diff(old["body"], new["body"])

    @app.post("/api/drafts/{did}/undo-revision")
    def undo_revision(did: int):
        draft = db.row("SELECT * FROM drafts WHERE id=?", (did,))
        info = db.loads(draft and draft.get("last_revision"), None)
        if not info:
            raise HTTPException(400, "没有可以撤回的 AI 修改")
        before = db.row("SELECT * FROM draft_versions WHERE id=? AND draft_id=?", (info["before"], did))
        with db.tx() as conn:
            conn.execute("INSERT INTO draft_versions(draft_id, title, body, created_at, origin) VALUES (?,?,?,?, 'undo-ai')",
                         (did, draft["title"], draft["body"], db.now()))
            conn.execute("UPDATE drafts SET title=?, body=?, last_revision=NULL, updated_at=? WHERE id=?",
                         (before["title"], before["body"], db.now(), did))
        return {"ok": True}

    # ------------------------------------------------------------ edit rounds and writing habits
    @app.get("/api/learning")
    def learning_state():
        state = learning.load()
        rounds = db.rows("""SELECT r.id, r.draft_id, r.trigger, r.stats, r.learned, r.updated_at, d.title
                            FROM edit_rounds r LEFT JOIN drafts d ON d.id=r.draft_id ORDER BY r.updated_at DESC LIMIT 100""")
        for r in rounds:
            r["stats"] = db.loads(r["stats"], {})
        busy = bool(db.row("SELECT 1 x FROM jobs WHERE kind='learn' AND state IN ('queued','running')"))
        return {**state, "rounds": rounds, "learning": busy, "pending": len(learning.pending_rounds())}

    @app.get("/api/learning/rounds/{rid}")
    def learning_round(rid: int):
        found = db.row("SELECT * FROM edit_rounds WHERE id=?", (rid,))
        if not found:
            raise HTTPException(404)
        found["changes"] = db.loads(found["changes"], [])
        return found

    @app.post("/api/learning/run")
    def learning_run():
        if db.row("SELECT 1 x FROM jobs WHERE kind='learn' AND state IN ('queued','running')"):
            return {"queued": False}
        for d in db.rows("SELECT id, body FROM drafts WHERE status='draft'"):
            learning.record_round(d["id"], d["body"], "manual")
        return {"queued": bool(jobs.enqueue("learn", {}))}

    @app.put("/api/learning/lessons")
    def learning_save(body: dict = Body(...)):
        state = learning.load()
        known = {l["id"]: l for l in state.get("lessons", [])}
        out = []
        for l in body.get("lessons") or []:
            rule = str(l.get("rule", "")).strip()
            if not rule:
                continue
            prev = known.get(str(l.get("id") or ""), {})
            out.append({**prev, "id": prev.get("id") or __import__("secrets").token_hex(4), "rule": rule[:300],
                        "enabled": bool(l.get("enabled", True)), "source": prev.get("source", "gary"),
                        "examples": prev.get("examples", []), "evidence": prev.get("evidence", 0)})
        state["lessons"] = out
        learning.save(state)
        return state

    @app.get("/api/drafts/{did}/cases")
    def list_cases(did: int):
        found = db.rows("SELECT * FROM case_searches WHERE draft_id=? ORDER BY id DESC LIMIT 20", (did,))
        for r in found:
            r["results"] = db.loads(r["results"], [])
        return found

    @app.post("/api/drafts/{did}/cases")
    def search_cases(did: int, body: dict = Body(...)):
        if not prompts.case_search_on(settings):
            raise HTTPException(400, "没有开案例搜索（设置里 case_search 需要是 scys）")
        query = str(body.get("query", "")).strip()
        if not query:
            raise HTTPException(400, "说一下要找什么样的案例")
        if not db.row("SELECT 1 x FROM drafts WHERE id=?", (did,)):
            raise HTTPException(404)
        with db.tx() as conn:
            sid = conn.execute("INSERT INTO case_searches(draft_id, query, created_at) VALUES (?,?,?)",
                               (did, query[:300], db.now())).lastrowid
        jobs.enqueue("cases", {"search_id": sid})
        return {"id": sid}

    # ------------------------------------------------------------ images
    @app.post("/api/drafts/{did}/images")
    async def upload_images(did: int, files: list[UploadFile] = File(...)):
        if not db.row("SELECT 1 x FROM drafts WHERE id=?", (did,)):
            raise HTTPException(404)
        saved = []
        for f in files[:20]:
            data = await f.read(images.MAX_UPLOAD + 1)
            try:
                saved.append(images.save(did, f.filename or "图片", data))
            except images.ImageError as exc:
                raise HTTPException(400, f"{f.filename}：{exc}")
        return [{k: v for k, v in im.items() if k != "file"} for im in saved]

    @app.get("/api/images/{iid}")
    def image_file(iid: int):
        found = images.get(iid)
        if not found or not images.path_of(found).is_file():
            raise HTTPException(404)
        return FileResponse(images.path_of(found), media_type=found["mime"],
                            headers={"Cache-Control": "private, max-age=86400, immutable"})

    @app.delete("/api/images/{iid}")
    def delete_image(iid: int):
        found = images.get(iid)
        if not found:
            raise HTTPException(404)
        draft = db.row("SELECT body FROM drafts WHERE id=?", (found["draft_id"],))
        if draft and iid in images.referenced(draft["body"]):
            raise HTTPException(400, "正文里还在用这张图，先从正文里删掉")
        images.delete(iid)
        return {"ok": True}

    @app.post("/api/preview")
    def preview(body: dict = Body(...)):
        platform = body.get("platform")
        text = str(body.get("body", ""))
        out = {"flags": prompts.voice_flags(settings, text, "X" if platform == "x" else "公众号")}
        if platform == "x":
            out["thread"] = thread_report(text)
        else:
            out["html"] = wechat_html(text)
        return out

    # ------------------------------------------------------------ publish
    @app.post("/api/drafts/{did}/publish")
    def publish_draft(did: int, body: dict = Body(...)):
        if body.get("confirm") is not True:
            raise HTTPException(400, "需要确认")
        draft = db.row("SELECT * FROM drafts WHERE id=?", (did,))
        if not draft:
            raise HTTPException(404)
        # 长文 (platform wechat) goes to the 公众号 draft box or to an X Article draft; X drafts are threads.
        target = body.get("target") or draft["platform"]
        allowed = ("x",) if draft["platform"] == "x" else ("wechat", "x_article", "zsxq")
        if target not in allowed:
            raise HTTPException(400, "这篇稿子不能发到这个平台")
        learning.record_round(did, draft["body"], "publish")
        try:
            if target == "x":
                result = publish.x_publish(store, draft["body"])
                remote = ",".join(result["ids"])
            elif target == "x_article":
                result = _push_x_article(draft)
                remote = result.get("article_id")
            elif target == "zsxq":
                result = _push_zsxq(draft)
                remote = result.get("article_id")
            else:
                result = _push_wechat(draft, body.get("cover_media_id"))
                remote = result["media_id"]
            state = "sent"
        except (publish.PublishError, images.ImageError) as exc:
            uncertain = getattr(exc, "uncertain", False)
            state = "unknown" if uncertain else "failed"
            with db.tx() as conn:
                conn.execute("INSERT INTO publications(draft_id, platform, state, detail, created_at) VALUES (?,?,?,?,?)",
                             (did, target, state, db.dumps({"error": str(exc), **getattr(exc, "detail", {})}), db.now()))
            raise HTTPException(502 if uncertain else 400, str(exc))
        with db.tx() as conn:
            conn.execute("INSERT INTO publications(draft_id, platform, state, remote_id, detail, created_at) VALUES (?,?,?,?,?,?)",
                         (did, target, state, remote, db.dumps(result), db.now()))
            if target == "x":
                conn.execute("UPDATE drafts SET status='published', updated_at=? WHERE id=?", (db.now(), did))
        return {"ok": True, **result}

    def _body_image(src_id: int, draft_id: int) -> dict:
        found = images.get(src_id)
        if not found or found["draft_id"] != draft_id:
            raise images.ImageError(f"正文里的第 {src_id} 号图片找不到了，重新插一次")
        return found

    def _wx_cover(image: dict) -> str:
        cached = images.remote(image["id"], "wx_cover")
        if cached:
            return cached["remote_id"]
        data, mime, name = images.encoded(image, max_bytes=WX_COVER_MAX)
        media_id = publish.wx_upload_material(store, data, mime, name)
        images.remember(image["id"], "wx_cover", media_id, None)
        return media_id

    def _push_wechat(draft: dict, override_cover: str | None) -> dict:
        html = wechat_html(draft["body"])
        for iid in images.referenced(draft["body"]):
            image = _body_image(iid, draft["id"])
            cached = images.remote(iid, "wx_inline")
            if cached:
                url = cached["url"]
            else:
                data, mime, name = images.encoded(image, max_bytes=WX_INLINE_MAX, max_side=2000)
                url = publish.wx_upload_inline(store, data, mime, name)
                images.remember(iid, "wx_inline", None, url)
            # media/uploadimg answers with http://; the same image is served over https
            url = re.sub(r"^http://", "https://", url)
            html = html.replace(f'src="/api/images/{iid}"', f'src="{url}"')
        stray = re.findall(r'<img [^>]*src="(?!https?://mmbiz\.qpic\.cn/)([^"]*)"', html)
        if stray:
            raise publish.PublishError(f"正文里有不是从工作台插入的图片（{stray[0][:60]}），公众号不会显示，先换成上传的图片")
        crop_235 = crop_11 = ""
        cover_image = images.get(draft["cover_wechat"]) if draft.get("cover_wechat") else None
        if cover_image:
            cover = _wx_cover(cover_image)
            crop_235, crop_11 = wechat_crops(cover_image["width"], cover_image["height"],
                                             db.loads(draft.get("cover_crop"), {}).get("square_x", 0.0))
        else:
            cover = override_cover or store.get("wx_default_cover")
        return publish.wx_draft(store, draft["title"], draft["body"], settings.author, cover, html=html,
                                summary=draft.get("summary") or "", source_url=draft.get("source_url") or "",
                                crop_235=crop_235, crop_11=crop_11)

    def _x_media(image: dict) -> str:
        cached = images.remote(image["id"], "x_media")
        if cached:
            return cached["remote_id"]
        data, mime, name = images.encoded(image, max_bytes=X_IMAGE_MAX)
        media_id, expires = publish.x_upload_image(store, data, mime, name)
        images.remember(image["id"], "x_media", media_id, None, expires)
        return media_id

    def _push_zsxq(draft: dict) -> dict:
        body = draft["body"]
        ids: list[str] = []
        for iid in images.referenced(body):
            image = _body_image(iid, draft["id"])
            cached = images.remote(iid, "zsxq")
            if cached and cached["url"]:
                image_id, link = cached["remote_id"], cached["url"]
            else:
                data, mime, name = images.encoded(image, max_bytes=9 * 1024 * 1024)
                image_id, link = zsxq.upload_image(store, data, mime, name)
                images.remember(iid, "zsxq", image_id, link)
            ids.append(image_id)
            body = body.replace(f"/api/images/{iid})", f"{link})")
        if images.REF_RE.search(body) or re.search(r"!\[[^\]]*\]\((?!https?://)", body):
            raise publish.PublishError("正文里有图片没传上去，先检查一下图片")
        # pushing again updates the same draft instead of adding another
        last = db.row("""SELECT remote_id FROM publications WHERE draft_id=? AND platform='zsxq' AND state='sent'
                         AND remote_id IS NOT NULL AND remote_id != '' ORDER BY id DESC LIMIT 1""", (draft["id"],))
        if not settings.zsxq_group_id:
            raise publish.PublishError("先在设置里填要存草稿的星球编号")
        return zsxq.save_draft(store, settings.zsxq_group_id, draft["title"], body, ids,
                               article_id=(last or {}).get("remote_id") or "")

    def _push_x_article(draft: dict) -> dict:
        title = draft["title"].strip()
        if not title:
            raise publish.PublishError("X 长文要有标题")

        def resolve(src: str) -> str:
            m = images.REF_RE.fullmatch(src.strip())
            if not m:
                raise publish.PublishError(f"正文里有不是从工作台插入的图片（{src[:60]}），先换成上传的图片")
            return _x_media(_body_image(int(m.group(1)), draft["id"]))

        state = xarticle.content_state(draft["body"], resolve)
        if not state["blocks"]:
            raise publish.PublishError("正文是空的")
        cover_id = draft.get("cover_x") or draft.get("cover_wechat")
        cover = _x_media(images.get(cover_id)) if cover_id and images.get(cover_id) else None
        return publish.x_article_draft(store, title, state, cover)

    # ------------------------------------------------------------ 小红书
    @app.post("/api/drafts/{did}/xhs/generate")
    def xhs_generate(did: int):
        draft = db.row("SELECT * FROM drafts WHERE id=?", (did,))
        if not draft:
            raise HTTPException(404)
        if not draft["body"].strip():
            raise HTTPException(400, "长文还是空的")
        state = xhs.load(draft)
        if state.get("generating"):
            return state
        state.update(generating=True, error=None)
        xhs.save(did, state)
        jobs.enqueue("xhs", {"draft_id": did})
        return state

    @app.put("/api/drafts/{did}/xhs")
    def xhs_update(did: int, body: dict = Body(...)):
        draft = db.row("SELECT * FROM drafts WHERE id=?", (did,))
        if not draft:
            raise HTTPException(404)
        state = xhs.load(draft)
        if "title" in body:
            state["title"] = str(body["title"])[:xhs.TITLE_MAX]
        if "body" in body:
            state["body"] = str(body["body"])[:xhs.BODY_MAX]
        if "tags" in body:
            state["tags"] = [str(t).lstrip("#").strip() for t in body["tags"] if str(t).strip()][:10]
        if isinstance(body.get("cover"), dict):
            state["cover"] = body["cover"]
        if isinstance(body.get("cards"), list):
            state["cards"] = [c for c in body["cards"] if isinstance(c, dict)][:6]
        if isinstance(body.get("extra_images"), list):
            mine = {im["id"] for im in images.for_draft(did)}
            state["extra_images"] = [int(i) for i in body["extra_images"] if int(i) in mine][:9]
        if isinstance(body.get("card_images"), list):  # reorder only
            known = set(state.get("card_images") or [])
            state["card_images"] = [int(i) for i in body["card_images"] if int(i) in known]
        xhs.save(did, state)
        return state

    @app.post("/api/drafts/{did}/xhs/render")
    def xhs_render(did: int):
        if not db.row("SELECT 1 x FROM drafts WHERE id=?", (did,)):
            raise HTTPException(404)
        return xhs.render(did, settings.author)

    @app.get("/api/drafts/{did}/xhs.zip")
    def xhs_zip(did: int):
        draft = db.row("SELECT * FROM drafts WHERE id=?", (did,))
        if not draft:
            raise HTTPException(404)
        state = xhs.load(draft)
        name = re.sub(r"[^\w\u4e00-\u9fff-]+", "", state.get("title") or "小红书")[:20] or "xhs"
        from urllib.parse import quote
        return Response(xhs.zip_bytes(state), media_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.zip"})

    # ------------------------------------------------------------ 知识星球
    @app.get("/api/zsxq")
    def zsxq_state():
        return {"connected": bool(store.get("zsxq_token")), "account": zsxq.account(store),
                "group": {"id": settings.zsxq_group_id, "name": settings.zsxq_group_name or settings.zsxq_group_id},
                "login": zsxq.login.status()}

    @app.put("/api/zsxq/group")
    def zsxq_group(body: dict = Body(...)):
        gid = str(body.get("id", "")).strip()
        if gid and not gid.isdigit():
            raise HTTPException(400, "星球编号是一串数字，在星球网页地址的 groupId= 后面")
        settings.zsxq_group_id = gid
        settings.zsxq_group_name = str(body.get("name", "")).strip()[:40]
        settings.save()
        return {"id": settings.zsxq_group_id, "name": settings.zsxq_group_name}

    @app.post("/api/zsxq/login")
    def zsxq_login(body: dict = Body(default={})):
        if body.get("agree") is not True:
            raise HTTPException(400, "需要先同意知识星球的用户协议和隐私政策")
        return zsxq.login.start(store)

    @app.get("/api/zsxq/login")
    def zsxq_login_status():
        return zsxq.login.status()

    @app.get("/api/zsxq/login/qr")
    def zsxq_login_qr():
        if not zsxq.login.qr:
            raise HTTPException(404)
        return Response(zsxq.login.qr, media_type="image/png")

    @app.post("/api/zsxq/disconnect")
    def zsxq_disconnect():
        store.set("zsxq_token", None)
        return {"ok": True}

    @app.get("/api/publications")
    def publications():
        return db.rows("""SELECT p.*, d.title FROM publications p LEFT JOIN drafts d ON d.id=p.draft_id
                          ORDER BY p.id DESC LIMIT 100""")

    @app.get("/api/publish/x/connect")
    def x_connect():
        try:
            return {"url": publish.x_authorize_url(store, settings.origins[0])}
        except publish.PublishError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/publish/x/callback")
    def x_callback(state: str = "", code: str = "", error: str = ""):
        if error:
            return RedirectResponse("/#/settings?x=denied")
        try:
            publish.x_finish_auth(store, state, code)
        except publish.PublishError:
            return RedirectResponse("/#/settings?x=failed")
        return RedirectResponse("/#/settings?x=ok")

    # ------------------------------------------------------------ settings
    @app.get("/api/settings")
    def get_settings():
        return {"author": settings.author, "rp_id": settings.rp_id, "origins": settings.origins,
                "login_mode": "access" if settings.access_login else "passkey", "access_emails": settings.access_emails,
                "x_redirect_uri": publish.x_redirect_uri(settings.origins[0]),
                "secrets": store.present(*SECRET_KEYS), "wechat_cover": bool(store.get("wx_default_cover")),
                "x_account": publish.x_account(store) if store.get("x_tokens") else None}

    @app.put("/api/settings/secrets")
    def set_secrets(body: dict = Body(...)):
        for key, value in body.items():
            if key in SECRET_KEYS:
                store.set(key, str(value).strip())
                if key.startswith("wx_"):
                    store.set("wx_token", None)
        return {"secrets": store.present(*SECRET_KEYS)}

    @app.put("/api/settings")
    def update_settings(body: dict = Body(...)):
        if "author" in body:
            settings.author = str(body["author"])[:16]
            settings.save()
        return {"ok": True}

    @app.post("/api/settings/x/disconnect")
    def x_disconnect():
        store.set("x_tokens", None)
        return {"ok": True}

    @app.post("/api/settings/wechat-cover")
    async def wechat_cover(file: UploadFile = File(...)):
        suffix = Path(file.filename or "cover.jpg").suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png"):
            raise HTTPException(400, "封面要 jpg 或 png")
        target = jobs.uploads_dir() / f"cover-{secrets.token_hex(4)}{suffix}"
        with target.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            media_id = publish.wx_upload_cover(store, target)
        except publish.PublishError as exc:
            raise HTTPException(400, str(exc))
        store.set("wx_default_cover", media_id)
        return {"ok": True}

    # ------------------------------------------------------------ frontend
    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        target = (WEB_DIST / path).resolve()
        if path and target.is_file() and WEB_DIST.resolve() in target.parents:
            return FileResponse(target)
        index = WEB_DIST / "index.html"
        if not index.exists():
            return Response("前端还没构建：在 web/ 目录运行 npm run build", media_type="text/plain; charset=utf-8")
        return FileResponse(index)

    return app


def wechat_crops(width: int, height: int, square_x: float) -> tuple[str, str]:
    """pic_crop_235_1 and pic_crop_1_1 for a cover: the widest centred 2.35:1
    region, and a square whose left edge slides with square_x (0 left … 1 right)."""
    def fmt(*v: float) -> str:
        return "_".join(f"{min(max(x, 0.0), 1.0):.6f}".rstrip("0").rstrip(".") or "0" for x in v)

    ratio = width / height
    if ratio >= 2.35:
        w = 2.35 / ratio
        wide = fmt((1 - w) / 2, 0, (1 + w) / 2, 1)
    else:
        h = ratio / 2.35
        wide = fmt(0, (1 - h) / 2, 1, (1 + h) / 2)
    if width >= height:
        w = height / width
        x0 = (1 - w) * min(max(square_x, 0.0), 1.0)
        square = fmt(x0, 0, x0 + w, 1)
    else:
        h = width / height
        square = fmt(0, (1 - h) / 2, 1, (1 + h) / 2)
    return wide, square


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="内容工作台")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="启动（默认）")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=None)
    conf = sub.add_parser("configure", help="设置访问地址")
    conf.add_argument("--tailnet-host", help="tailscale serve 给的域名，例如 macmini.tail1234.ts.net")
    conf.add_argument("--local", action="store_true", help="改回只在本机用 http://localhost")
    conf.add_argument("--public-host", help="经 Cloudflare Tunnel 访问的域名，例如 write.example.com；必须同时配 Access")
    conf.add_argument("--access-team", help="Cloudflare Zero Trust 团队名（xxx.cloudflareaccess.com 里的 xxx）")
    conf.add_argument("--access-aud", help="Access 应用的 AUD 标签")
    conf.add_argument("--access-email", action="append", help="允许的邮箱，可重复")
    conf.add_argument("--claude-token-file", help="存放 claude setup-token 令牌的文件（权限需为 600）")
    conf.add_argument("--login", choices=["access", "passkey"],
                      help="access：收到邮箱验证码就算登录（需要已配 Cloudflare Access）；passkey：再加一道 passkey")
    sub.add_parser("setup-code", help="显示首次注册用的初始化码")
    args = parser.parse_args()
    settings = Settings.load()

    if args.cmd == "configure":
        if args.tailnet_host:
            host = args.tailnet_host.strip().lower()
            if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net", host):
                raise SystemExit("这不像 tailscale 的 ts.net 域名")
            settings.rp_id = host
            settings.origins = [f"https://{host}"]
            settings.allowed_hosts = [host]
        elif args.public_host:
            host = args.public_host.strip().lower()
            if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
                raise SystemExit("域名格式不对")
            settings.rp_id = host
            settings.origins = [f"https://{host}"]
            settings.allowed_hosts = [host]
        elif args.local:
            settings.rp_id = "localhost"
            settings.origins = [f"http://localhost:{settings.port}"]
            settings.allowed_hosts = [f"localhost:{settings.port}", f"127.0.0.1:{settings.port}"]
            settings.access_team = settings.access_aud = ""
        if args.access_team:
            settings.access_team = args.access_team.strip().lower()
        if args.access_aud:
            settings.access_aud = args.access_aud.strip()
        if args.access_email:
            settings.access_emails = [e.strip().lower() for e in args.access_email]
        if args.public_host and not (settings.access_team and settings.access_aud):
            raise SystemExit("公网域名必须同时配好 Cloudflare Access（--access-team 和 --access-aud），否则不保存")
        if args.login:
            if args.login == "access" and not (settings.access_team and settings.access_aud):
                raise SystemExit("只用邮箱验证码登录，必须先配好 Cloudflare Access（--access-team 和 --access-aud）")
            settings.login_mode = args.login
        if args.claude_token_file:
            token = Path(args.claude_token_file).expanduser().resolve()
            if not token.is_file() or token.stat().st_mode & 0o077:
                raise SystemExit("令牌文件不存在，或者权限不是 600")
            settings.claude_token_file = str(token)
        settings.save()
        if settings.access_login:
            print(f"访问地址：{settings.origins[0]}  （登录方式：收邮箱验证码，Cloudflare Access 放行的邮箱 {', '.join(settings.access_emails)}）")
            return
        print(f"访问地址：{settings.origins[0]}  （passkey 绑定在 {settings.rp_id} 上，换地址后需要在新地址重新注册）")
        if not auth.has_credentials(settings.rp_id):
            ensure_setup_code()
            print("这个地址上还没有 passkey。运行 content-workbench setup-code 取初始化码，在新地址上注册。")
        return

    if args.cmd == "setup-code":
        print(ensure_setup_code())
        return

    host = getattr(args, "host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("只允许监听本机地址；跨设备访问请用 tailscale serve 转发，见 README")
    port = getattr(args, "port", None) or settings.port
    if not auth.has_credentials(settings.rp_id):
        print(f"\n首次使用：打开 {settings.origins[0]}，初始化码用 content-workbench setup-code 查看\n")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info", proxy_headers=False)


if __name__ == "__main__":
    main()
