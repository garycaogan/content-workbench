"""知识星球: save a long-form draft into 「我的文章-草稿箱」 of a group.

There is no official API. The web app (wx.zsxq.com) talks to api.zsxq.com with
the login cookie ``zsxq_access_token`` and a signature header: SHA1 of
"<full url> <unix seconds> <request id>". We call the same endpoints:

- ``POST v2/uploads`` → a Qiniu upload token; POST the picture there → image_id + link
- ``POST v2/articles/drafts`` → saves the draft (title, HTML, Markdown, image ids);
  passing the previous ``article_id`` updates that draft instead of adding one

Login is the web QR code, scanned with WeChat: a headless browser opens the
login page and the workbench shows the author the QR. Agreeing to 知识星球's user
agreement on that page happens only after the author ticks it in the workbench."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid

import httpx

from .config import SecretStore
from .publish import PublishError
from .textutil import markdown_parser

API = "https://api.zsxq.com/v2/"
UPLOAD_FALLBACK = "https://upload-z1.qiniup.com"
LOGIN_URL = "https://wx.zsxq.com/login"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
VERSION_V2, VERSION_V3 = "2.98.0", "3.24.0"
# endpoints the web app sends to v3 instead of v2
V3_PATHS = re.compile(r"^(users/self|users/self/login_info|users/\w+/statistics|users/self/accounts)$")
TITLE_MAX = 60


def _url(path: str) -> str:
    url = API + path
    return url.replace("/v2/", "/v3/") if V3_PATHS.match(path.split("?")[0]) else url


def headers(url: str, aduid: str, now: float | None = None, request_id: str | None = None) -> dict:
    ts = str(int(now if now is not None else time.time()))
    rid = request_id or str(uuid.uuid4())
    sig = hashlib.sha1(f"{url} {ts} {rid}".encode("utf-8")).hexdigest()
    return {"X-Request-Id": rid, "X-Version": VERSION_V3 if "/v3/" in url else VERSION_V2, "X-Signature": sig,
            "X-Timestamp": ts, "X-Aduid": aduid, "Origin": "https://wx.zsxq.com", "Referer": "https://wx.zsxq.com/",
            "User-Agent": UA, "Accept": "application/json, text/plain, */*"}


def _aduid(store: SecretStore) -> str:
    found = store.get("zsxq_aduid")
    if not found:
        found = str(uuid.uuid4())
        store.set("zsxq_aduid", found)
    return found


def call(store: SecretStore, method: str, path: str, body: dict | None = None) -> dict:
    token = store.get("zsxq_token")
    if not token:
        raise PublishError("知识星球还没连接，先去设置里扫码")
    url = _url(path)
    h = headers(url, _aduid(store))
    if body is not None:
        h["Content-Type"] = "application/json"
    try:
        resp = httpx.request(method, url, headers=h, cookies={"zsxq_access_token": token},
                             content=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
                             timeout=60)
    except httpx.HTTPError as exc:
        raise PublishError(f"连不上知识星球：{exc}", uncertain=method != "GET") from None
    try:
        data = resp.json()
    except ValueError:
        raise PublishError(f"知识星球返回了看不懂的内容：{resp.status_code} {resp.text[:200]}") from None
    if not data.get("succeeded"):
        code = data.get("code")
        if code in (401, 1059, 14210):
            raise PublishError("知识星球的登录过期了，去设置里重新扫一次码")
        raise PublishError(f"知识星球返回错误：{code} {data.get('info') or data.get('error') or ''}".strip())
    return data.get("resp_data") or {}


def account(store: SecretStore) -> dict | None:
    if not store.get("zsxq_token"):
        return None
    try:
        user = call(store, "GET", "users/self").get("user") or {}
    except PublishError:
        return None
    return {"name": user.get("name"), "user_id": user.get("user_id")}


def upload_image(store: SecretStore, data: bytes, mime: str, name: str) -> tuple[str, str]:
    """Returns (image_id, link)."""
    # usage=article is what the article editor sends; without it the upload returns no link
    info = call(store, "POST", "uploads", {"req_data": {"type": "image", "usage": "article"}})
    token = info.get("upload_token")
    if not token:
        raise PublishError("知识星球没给图片上传凭证")
    zone = ((info.get("upload_zone") or {}).get("domains") or [UPLOAD_FALLBACK])[0]
    url = zone if zone.startswith("http") else f"https://{zone}"
    try:
        resp = httpx.post(url, data={"token": token}, files={"file": (name, data, mime)}, timeout=120,
                          headers={"User-Agent": UA, "Origin": "https://wx.zsxq.com", "Referer": "https://wx.zsxq.com/"})
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PublishError(f"图片传到知识星球失败：{exc}") from None
    image_id = (body.get("resp_data") or {}).get("image_id")
    link = body.get("link") or (body.get("resp_data") or {}).get("link")
    if not body.get("succeeded") or not image_id or not link:
        raise PublishError(f"图片传到知识星球失败：{json.dumps(body, ensure_ascii=False)[:200]}")
    return str(image_id), str(link)


_md = markdown_parser()


def article_html(markdown: str) -> str:
    """Plain HTML like the article editor produces; highlights become bold
    (the 星球 article editor has no highlight)."""
    html = _md.render(markdown)
    return html.replace("<mark>", "<strong>").replace("</mark>", "</strong>").strip()


def save_draft(store: SecretStore, group_id: str, title: str, markdown: str, image_ids: list[str],
               article_id: str = "") -> dict:
    title = title.strip()
    if not title:
        raise PublishError("星球文章要有标题")
    if len(title) > TITLE_MAX:
        raise PublishError(f"星球文章标题最多 {TITLE_MAX} 个字，现在 {len(title)} 个")
    body = {"req_data": {"group_id": str(group_id), "article_id": article_id or "", "title": title,
                         "content": article_html(markdown), "image_ids": image_ids, "original_content": markdown}}
    data = call(store, "POST", "articles/drafts", body)
    found = data.get("article_id") or (data.get("draft") or {}).get("article_id") or (data.get("article") or {}).get("article_id")
    return {"article_id": str(found or article_id or ""), "url": f"https://wx.zsxq.com/article?groupId={group_id}"}


# ------------------------------------------------------------------ QR login

class Login:
    """One QR login at a time, run in a headless browser on this Mac."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = {"state": "idle"}
        self.qr: bytes | None = None

    def start(self, store: SecretStore) -> dict:
        with self.lock:
            if self.state.get("state") in ("starting", "waiting"):
                return self.status()
            self.state = {"state": "starting", "at": time.time()}
            self.qr = None
        threading.Thread(target=self._run, args=(store,), daemon=True, name="zsxq-login").start()
        return self.status()

    def status(self) -> dict:
        return {**self.state, "has_qr": self.qr is not None}

    def _set(self, **kw) -> None:
        self.state = {**self.state, **kw}

    def _run(self, store: SecretStore) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._set(state="failed", error="这台机器还没装后台浏览器（playwright）")
            return
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                ctx = browser.new_context(viewport={"width": 1280, "height": 860}, locale="zh-CN", user_agent=UA)
                page = ctx.new_page()
                page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
                page.get_by_text("获取登录二维码").first.click()
                agree = page.get_by_role("button", name="同意")
                if agree.count():  # the author agreed to 知识星球's terms in the workbench before this started
                    agree.first.click()
                else:
                    page.get_by_text("同意", exact=True).first.click(timeout=5000)
                page.wait_for_timeout(2500)
                self._set(state="waiting")
                deadline = time.time() + 240
                while time.time() < deadline:
                    token = next((c["value"] for c in ctx.cookies() if c["name"] == "zsxq_access_token"), None)
                    if token:
                        store.set("zsxq_token", token)
                        who = account(store) or {}
                        self._set(state="ok", name=who.get("name"))
                        browser.close()
                        return
                    if page.get_by_text("二维码已失效").count() or page.get_by_text("刷新").count():
                        try:
                            page.get_by_text("刷新").first.click(timeout=2000)
                            page.wait_for_timeout(2000)
                        except Exception:
                            pass
                    if "/login" in page.url:
                        self.qr = page.screenshot(clip={"x": 470, "y": 250, "width": 340, "height": 340})
                    page.wait_for_timeout(2000)
                self._set(state="expired")
                browser.close()
        except Exception as exc:  # reported to the settings page
            self._set(state="failed", error=str(exc)[:300])


login = Login()
