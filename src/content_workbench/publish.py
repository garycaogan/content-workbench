"""Distribution to X (official API, OAuth 2.0 user context) and to the 公众号
draft box (draft/add). Credentials are entered by the author on the settings page
and stored in the owner-only secret file.

Safety rules:
- Nothing is sent without an explicit confirmation from the editor.
- A send whose outcome is unclear is recorded as ``unknown`` and never retried
  automatically; a partial X thread records which posts went out.
- 公众号 only receives a draft; publishing stays a manual step in WeChat
  (personal-subject accounts cannot call freepublish)."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .config import SecretStore
from .textutil import digest, split_thread, wechat_html, x_weighted_length, X_LIMIT

X_AUTHORIZE = "https://x.com/i/oauth2/authorize"
X_TOKEN = "https://api.x.com/2/oauth2/token"
X_TWEETS = "https://api.x.com/2/tweets"
X_ME = "https://api.x.com/2/users/me"
X_MEDIA = "https://api.x.com/2/media/upload"
X_ARTICLE_DRAFT = "https://api.x.com/2/articles/draft"
# media.write is needed for pictures in X Articles; tokens granted before it was
# added have to reconnect once.
X_SCOPES = "tweet.read tweet.write users.read media.write offline.access"

WX_TOKEN = "https://api.weixin.qq.com/cgi-bin/stable_token"
WX_ADD_MATERIAL = "https://api.weixin.qq.com/cgi-bin/material/add_material"
WX_DRAFT_ADD = "https://api.weixin.qq.com/cgi-bin/draft/add"
WX_UPLOAD_IMG = "https://api.weixin.qq.com/cgi-bin/media/uploadimg"


class PublishError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False, detail: dict | None = None):
        super().__init__(message)
        self.uncertain = uncertain
        self.detail = detail or {}


# ---------------------------------------------------------------- X

def x_redirect_uri(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/publish/x/callback"


def x_authorize_url(store: SecretStore, base_url: str) -> str:
    client_id = store.get("x_client_id")
    if not client_id:
        raise PublishError("先在设置里填 X 的 Client ID")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    store.set("x_pkce", {"state": state, "verifier": verifier, "created": time.time(), "redirect": x_redirect_uri(base_url)})
    return X_AUTHORIZE + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": x_redirect_uri(base_url),
        "scope": X_SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def _x_token_request(store: SecretStore, data: dict) -> dict:
    client_id = store.get("x_client_id")
    secret = store.get("x_client_secret")
    auth = (client_id, secret) if secret else None
    if not secret:
        data = {**data, "client_id": client_id}
    resp = httpx.post(X_TOKEN, data=data, auth=auth, timeout=30)
    if resp.status_code != 200:
        raise PublishError(f"X 授权失败：{resp.status_code} {resp.text[:200]}")
    tok = resp.json()
    previous = store.get("x_tokens") or {}
    store.set("x_tokens", {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                           "expires_at": time.time() + int(tok.get("expires_in", 7200)) - 60,
                           "scope": tok.get("scope") or previous.get("scope", "")})
    return tok


def x_scopes(store: SecretStore) -> set[str]:
    return set(((store.get("x_tokens") or {}).get("scope") or "").split())


def x_finish_auth(store: SecretStore, state: str, code: str) -> None:
    pkce = store.get("x_pkce") or {}
    if not pkce or not secrets.compare_digest(str(pkce.get("state")), str(state)) or time.time() - pkce.get("created", 0) > 600:
        raise PublishError("X 授权状态不匹配或已过期，请重新连接")
    store.set("x_pkce", None)
    _x_token_request(store, {"grant_type": "authorization_code", "code": code,
                             "redirect_uri": pkce["redirect"], "code_verifier": pkce["verifier"]})


def _x_access_token(store: SecretStore) -> str:
    tokens = store.get("x_tokens")
    if not tokens:
        raise PublishError("X 还没连接")
    if tokens["expires_at"] < time.time():
        if not tokens.get("refresh_token"):
            raise PublishError("X 授权过期了，请重新连接")
        _x_token_request(store, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        tokens = store.get("x_tokens")
    return tokens["access_token"]


def x_account(store: SecretStore) -> dict | None:
    try:
        token = _x_access_token(store)
    except PublishError:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.get(X_ME, params={"user.fields": "subscription_type,verified_type"}, headers=headers, timeout=20)
    if resp.status_code == 400:  # field set not accepted: fall back to the plain profile
        resp = httpx.get(X_ME, headers=headers, timeout=20)
    return resp.json().get("data") if resp.status_code == 200 else None


def x_upload_image(store: SecretStore, data: bytes, mime: str, name: str) -> tuple[str, float]:
    """Upload one picture; returns (media id, unix time it stops being usable)."""
    if "media.write" not in x_scopes(store):
        raise PublishError("X 的授权里还没有上传图片的权限，去设置里点一次“重新连接”就好")
    token = _x_access_token(store)
    try:
        resp = httpx.post(X_MEDIA, headers={"Authorization": f"Bearer {token}"}, data={"media_category": "tweet_image"},
                          files={"media": (name, data, mime)}, timeout=120)
    except httpx.HTTPError as exc:
        raise PublishError(f"图片传到 X 时网络中断：{exc}") from None
    if resp.status_code not in (200, 201):
        raise PublishError(f"图片传到 X 失败：{resp.status_code} {resp.text[:200]}")
    body = resp.json().get("data") or {}
    if not body.get("id"):
        raise PublishError(f"图片传到 X 没拿到编号：{resp.text[:200]}")
    return str(body["id"]), time.time() + int(body.get("expires_after_secs") or 86400) - 600


def x_article_draft(store: SecretStore, title: str, content_state: dict, cover_media_id: str | None) -> dict:
    """Create a draft Article. Publishing stays a manual step on X."""
    token = _x_access_token(store)
    payload: dict = {"title": title, "content_state": content_state}
    if cover_media_id:
        payload["cover_media"] = {"media_category": "tweet_image", "media_id": cover_media_id}
    try:
        resp = httpx.post(X_ARTICLE_DRAFT, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    except httpx.HTTPError as exc:
        raise PublishError("推到 X 长文草稿时网络中断，结果不确定，请到 X 的长文草稿里看一眼", uncertain=True,
                           detail={"error": str(exc)}) from None
    if resp.status_code not in (200, 201):
        hint = "（X 长文需要发帖账号开通 Premium）" if resp.status_code == 403 else ""
        raise PublishError(f"推到 X 长文草稿失败：{resp.status_code} {resp.text[:300]}{hint}",
                           uncertain=resp.status_code >= 500)
    data = resp.json().get("data") or {}
    return {"article_id": data.get("id"), "url": "https://x.com/compose/articles"}


def x_publish(store: SecretStore, body: str) -> dict:
    posts = split_thread(body)
    if not posts:
        raise PublishError("没有可发的内容")
    for i, p in enumerate(posts, 1):
        if x_weighted_length(p) > X_LIMIT:
            raise PublishError(f"第 {i} 条超过 X 字数上限")
    token = _x_access_token(store)
    sent: list[str] = []
    reply_to = None
    for i, text in enumerate(posts, 1):
        payload = {"text": text}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}
        try:
            resp = httpx.post(X_TWEETS, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        except httpx.HTTPError as exc:
            raise PublishError(f"第 {i} 条发送时网络中断，结果不确定，请到 X 上看一眼再决定", uncertain=True,
                               detail={"sent_ids": sent, "error": str(exc)}) from None
        if resp.status_code not in (200, 201):
            raise PublishError(f"第 {i} 条发送失败：{resp.status_code} {resp.text[:200]}",
                               uncertain=resp.status_code >= 500, detail={"sent_ids": sent})
        reply_to = resp.json()["data"]["id"]
        sent.append(reply_to)
    return {"ids": sent, "url": f"https://x.com/i/status/{sent[0]}"}


# ---------------------------------------------------------------- 公众号

def _wx_token(store: SecretStore) -> str:
    cached = store.get("wx_token")
    if cached and cached["expires_at"] > time.time():
        return cached["access_token"]
    appid, secret = store.get("wx_appid"), store.get("wx_secret")
    if not appid or not secret:
        raise PublishError("先在设置里填公众号的 AppID 和 AppSecret")
    resp = httpx.post(WX_TOKEN, content=json.dumps({"grant_type": "client_credential", "appid": appid, "secret": secret}),
                      headers={"Content-Type": "application/json"}, timeout=20)
    data = resp.json()
    if "access_token" not in data:
        hint = "（如果是 40164，说明这台机器的出口 IP 不在公众号后台的 IP 白名单里）" if data.get("errcode") == 40164 else ""
        raise PublishError(f"公众号取 token 失败：{data.get('errcode')} {data.get('errmsg')}{hint}")
    store.set("wx_token", {"access_token": data["access_token"], "expires_at": time.time() + int(data.get("expires_in", 7200)) - 120})
    return data["access_token"]


def wx_upload_cover(store: SecretStore, image: Path) -> str:
    mime = "image/jpeg" if image.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return wx_upload_material(store, image.read_bytes(), mime, image.name)


def wx_upload_material(store: SecretStore, data: bytes, mime: str, name: str) -> str:
    """Permanent image material (what a draft's cover must be)."""
    token = _wx_token(store)
    try:
        resp = httpx.post(WX_ADD_MATERIAL, params={"access_token": token, "type": "image"},
                          files={"media": (name, data, mime)}, timeout=60)
    except httpx.HTTPError as exc:
        raise PublishError(f"封面传到公众号时网络中断：{exc}") from None
    body = resp.json()
    if "media_id" not in body:
        raise PublishError(f"封面上传失败：{body.get('errcode')} {body.get('errmsg')}")
    return body["media_id"]


def wx_upload_inline(store: SecretStore, data: bytes, mime: str, name: str) -> str:
    """Picture inside the article; 公众号 only shows images hosted by WeChat."""
    token = _wx_token(store)
    try:
        resp = httpx.post(WX_UPLOAD_IMG, params={"access_token": token}, files={"media": (name, data, mime)}, timeout=60)
    except httpx.HTTPError as exc:
        raise PublishError(f"正文图片传到公众号时网络中断：{exc}") from None
    body = resp.json()
    if not body.get("url"):
        raise PublishError(f"正文图片上传失败：{body.get('errcode')} {body.get('errmsg')}")
    return body["url"]


def wx_draft(store: SecretStore, title: str, markdown: str, author: str, cover_media_id: str, *,
             html: str | None = None, summary: str = "", source_url: str = "",
             crop_235: str = "", crop_11: str = "") -> dict:
    if not cover_media_id:
        raise PublishError("公众号草稿必须有封面，先在设置里上传默认封面或给这篇选一张")
    token = _wx_token(store)
    article = {"title": title[:64], "author": author[:16], "digest": (summary.strip() or digest(markdown))[:120],
               "content": html if html is not None else wechat_html(markdown),
               "thumb_media_id": cover_media_id, "need_open_comment": 1, "only_fans_can_comment": 0}
    if source_url:
        article["content_source_url"] = source_url
    if crop_235:
        article["pic_crop_235_1"] = crop_235
    if crop_11:
        article["pic_crop_1_1"] = crop_11
    try:
        resp = httpx.post(WX_DRAFT_ADD, params={"access_token": token},
                          content=json.dumps({"articles": [article]}, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json; charset=utf-8"}, timeout=60)
    except httpx.HTTPError as exc:
        raise PublishError("推送草稿时网络中断，结果不确定，请到公众号草稿箱看一眼", uncertain=True,
                           detail={"error": str(exc)}) from None
    data = resp.json()
    if "media_id" not in data:
        raise PublishError(f"推送草稿失败：{data.get('errcode')} {data.get('errmsg')}")
    return {"media_id": data["media_id"]}
