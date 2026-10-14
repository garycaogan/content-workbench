"""Long-form drafts: pictures, covers, the 公众号 draft box and X Article drafts."""

import io

from PIL import Image

from content_workbench import publish
from content_workbench.app import wechat_crops
from content_workbench.config import SecretStore
from content_workbench.xarticle import content_state

from test_app import ORIGIN, client, login

H = {"Origin": ORIGIN}


def png(w=470, h=200, color=(20, 30, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "PNG")
    return out.getvalue()


def new_longform(c, body=""):
    return c.post("/api/drafts", json={"platform": "wechat", "title": "长文", "body": body}, headers=H).json()["id"]


def upload(c, did, *blobs):
    files = [("files", (f"p{i}.png", b, "image/png")) for i, b in enumerate(blobs)]
    r = c.post(f"/api/drafts/{did}/images", files=files, headers=H)
    assert r.status_code == 200, r.text
    return [im["id"] for im in r.json()]


def test_images_upload_serve_cover_and_delete():
    c = client()
    login(c)
    did = new_longform(c)
    a, b = upload(c, did, png(), png(500, 200))
    r = c.get(f"/api/images/{a}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "immutable" in r.headers["cache-control"]
    assert c.post(f"/api/drafts/{did}/images", files=[("files", ("x.png", b"not an image", "image/png"))], headers=H).status_code == 400

    c.put(f"/api/drafts/{did}", json={"body": f"正文\n\n![图](/api/images/{a})", "cover_wechat": b, "cover_x": b,
                                      "cover_crop": {"square_x": 0.5}, "summary": "摘要", "source_url": "https://github.com/x/y"}, headers=H)
    d = c.get(f"/api/drafts/{did}").json()
    assert (d["cover_wechat"], d["cover_x"], d["cover_crop"], d["summary"]) == (b, b, {"square_x": 0.5}, "摘要")
    assert len(d["images"]) == 2 and "file" not in d["images"][0]
    assert c.put(f"/api/drafts/{did}", json={"source_url": "github.com"}, headers=H).status_code == 400

    other = new_longform(c)
    assert c.put(f"/api/drafts/{other}", json={"cover_wechat": a}, headers=H).status_code == 400
    assert c.delete(f"/api/images/{a}", headers=H).status_code == 400  # still in the body
    assert c.delete(f"/api/images/{b}", headers=H).status_code == 200
    assert c.get(f"/api/drafts/{did}").json()["cover_wechat"] is None


def test_wechat_crops():
    assert wechat_crops(2350, 1000, 0) == ("0_0_1_1", "0_0_0.425532_1")
    wide, square = wechat_crops(2500, 1000, 1)
    assert wide == "0.03_0_0.97_1" and square == "0.6_0_1_1"


def test_push_to_wechat_uploads_pictures_once(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    pic, cover = upload(c, did, png(), png(470, 200))
    c.put(f"/api/drafts/{did}", json={"body": f"## 一、开头\n\n第一段\n\n![](/api/images/{pic})\n\n再来一次 ![](/api/images/{pic})",
                                      "cover_wechat": cover, "source_url": "https://github.com/x/y"}, headers=H)
    calls = {"inline": 0, "material": 0}

    def inline(store, data, mime, name):
        calls["inline"] += 1
        assert mime in ("image/png", "image/jpeg") and len(data) < 1024 * 1024
        return "http://mmbiz.qpic.cn/pic/1"  # what WeChat really returns

    def material(store, data, mime, name):
        calls["material"] += 1
        return "COVER_MEDIA"

    sent = {}

    def draft(store, title, markdown, author, cover_media_id, **kw):
        sent.update(kw, cover=cover_media_id)
        return {"media_id": "DRAFT1"}

    monkeypatch.setattr(publish, "wx_upload_inline", inline)
    monkeypatch.setattr(publish, "wx_upload_material", material)
    monkeypatch.setattr(publish, "wx_draft", draft)
    for _ in range(2):
        r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "wechat"}, headers=H)
        assert r.status_code == 200, r.text
    assert calls == {"inline": 1, "material": 1}
    assert sent["cover"] == "COVER_MEDIA" and sent["crop_11"] == "0_0_0.425532_1" and sent["source_url"] == "https://github.com/x/y"
    assert sent["html"].count('src="https://mmbiz.qpic.cn/pic/1"') == 2 and "/api/images/" not in sent["html"]
    assert c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "x"}, headers=H).status_code == 400


def test_push_to_wechat_rejects_outside_pictures(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c, "![](https://example.com/a.png)")
    monkeypatch.setattr(publish, "wx_draft", lambda *a, **k: {"media_id": "X"})
    r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True}, headers=H)
    assert r.status_code == 400 and "不是从工作台插入的图片" in r.json()["detail"]


def test_x_article_content_state():
    md = ("## 一、标题\n\n这是**加粗**和*斜体*，链接 [仓库](https://github.com/a/b)，还有 https://x.com/y 。\n\n"
          "- 第一项\n- 第二项\n\n> 引用一句\n\n![说明](/api/images/7)\n\n---\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n收尾 😀**粗**")
    state = content_state(md, lambda src: "M" + src.rsplit("/", 1)[-1])
    types = [b["type"] for b in state["blocks"]]
    assert types == ["header-one", "unstyled", "unordered-list-item", "unordered-list-item", "blockquote",
                     "atomic", "atomic", "atomic", "unstyled"]
    para = state["blocks"][1]
    assert [s["style"] for s in para["inline_style_ranges"]] == ["bold", "italic"]
    assert para["text"][para["inline_style_ranges"][0]["offset"]:][:2] == "加粗"
    links = [state["entities"][r["key"]]["value"]["data"]["url"] for r in para["entity_ranges"]]
    assert links == ["https://github.com/a/b", "https://x.com/y"]
    image = state["entities"][state["blocks"][5]["entity_ranges"][0]["key"]]["value"]
    assert image["type"] == "image" and image["data"]["media_items"][0]["media_id"] == "M7" and image["data"]["caption"] == "说明"
    kinds = [state["entities"][b["entity_ranges"][0]["key"]]["value"]["type"] for b in state["blocks"][6:8]]
    assert kinds == ["divider", "markdown"]
    last = state["blocks"][-1]
    assert last["inline_style_ranges"] == [{"offset": 5, "length": 1, "style": "bold"}]  # emoji is two UTF-16 units


def test_push_to_x_article(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    pic, cover_w, cover_x = upload(c, did, png(), png(470, 200), png(500, 200))
    c.put(f"/api/drafts/{did}", json={"body": f"第一段\n\n![](/api/images/{pic})", "cover_wechat": cover_w, "cover_x": cover_x}, headers=H)
    uploaded = []
    monkeypatch.setattr(publish, "x_upload_image", lambda store, data, mime, name: (uploaded.append(name) or f"m{len(uploaded)}", 9e12))
    got = {}
    monkeypatch.setattr(publish, "x_article_draft", lambda store, title, state, cover: got.update(title=title, state=state, cover=cover) or {"article_id": "42"})
    r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "x_article"}, headers=H)
    assert r.status_code == 200, r.text
    assert got["cover"] == "m2" and got["title"] == "长文" and len(uploaded) == 2
    pub = c.get(f"/api/drafts/{did}").json()["publications"][0]
    assert (pub["platform"], pub["state"], pub["remote_id"]) == ("x_article", "sent", "42")


def test_x_image_upload_needs_media_scope():
    store = SecretStore()
    store.set("x_tokens", {"access_token": "t", "refresh_token": "r", "expires_at": 9e12})
    try:
        publish.x_upload_image(store, b"x", "image/png", "a.png")
    except publish.PublishError as exc:
        assert "重新连接" in str(exc)
    else:
        raise AssertionError("expected a reconnect hint")


def test_zsxq_signature_matches_the_web_app():
    from content_workbench import zsxq
    h = zsxq.headers("https://api.zsxq.com/v2/articles/drafts", "aduid-1", now=1700000000, request_id="rid-1")
    import hashlib
    assert h["X-Signature"] == hashlib.sha1(b"https://api.zsxq.com/v2/articles/drafts 1700000000 rid-1").hexdigest()
    assert h["X-Version"] == "2.98.0" and h["X-Timestamp"] == "1700000000"
    assert zsxq._url("users/self") == "https://api.zsxq.com/v3/users/self"
    assert zsxq.headers(zsxq._url("users/self"), "a")["X-Version"] == "3.24.0"


def test_push_to_zsxq_uploads_once_and_updates_the_same_draft(monkeypatch):
    from content_workbench import zsxq
    c = client(zsxq_group_id="123456789", zsxq_group_name="示例星球")
    login(c)
    did = new_longform(c)
    pic, = upload(c, did, png())
    c.put(f"/api/drafts/{did}", json={"body": f"## 一、开头\n\n**重点** ==高亮==\n\n![](/api/images/{pic})"}, headers=H)
    SecretStore().set("zsxq_token", "tok")
    uploads, saved = [], []
    monkeypatch.setattr(zsxq, "upload_image", lambda store, data, mime, name: (uploads.append(name) or ("img9", "https://images.zsxq.com/x.jpg")))

    def fake_call(store, method, path, body=None):
        saved.append(body["req_data"])
        return {"article_id": "art1"}
    monkeypatch.setattr(zsxq, "call", fake_call)
    for _ in range(2):
        r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "zsxq"}, headers=H)
        assert r.status_code == 200, r.text
    assert len(uploads) == 1 and saved[0]["article_id"] == "" and saved[1]["article_id"] == "art1"
    req = saved[0]
    assert req["group_id"] == "123456789" and req["image_ids"] == ["img9"] and req["title"] == "长文"
    assert "![](https://images.zsxq.com/x.jpg)" in req["original_content"]
    assert '<img src="https://images.zsxq.com/x.jpg"' in req["content"] and "<mark>" not in req["content"]
    assert "<h2>一、开头</h2>" in req["content"]


def test_zsxq_upload_asks_for_article_usage_and_needs_a_link(monkeypatch):
    from content_workbench import zsxq, publish
    store = SecretStore()
    sent = {}
    monkeypatch.setattr(zsxq, "call", lambda st, m, path, body=None: sent.update(body=body) or {"upload_token": "t"})

    class Resp:
        def __init__(self, body): self._b = body
        def json(self): return self._b
    monkeypatch.setattr(zsxq.httpx, "post", lambda *a, **k: Resp({"succeeded": True, "resp_data": {"image_id": 7}}))
    try:
        zsxq.upload_image(store, b"x", "image/png", "a.png")
    except publish.PublishError:
        pass
    else:
        raise AssertionError("an upload without a link must fail loudly")
    assert sent["body"]["req_data"] == {"type": "image", "usage": "article"}
    monkeypatch.setattr(zsxq.httpx, "post", lambda *a, **k: Resp({"succeeded": True, "link": "https://article-images.zsxq.com/k", "resp_data": {"image_id": 7}}))
    assert zsxq.upload_image(store, b"x", "image/png", "a.png") == ("7", "https://article-images.zsxq.com/k")
