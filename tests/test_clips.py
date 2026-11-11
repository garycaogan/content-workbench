"""Videos in a long-form body: upload in pieces, processing, and what each
platform gets (X: a playable video block; 公众号 / 星球: the still frame; Feishu: a player)."""

import subprocess
import time

import pytest

from content_workbench import clips, db, feishu, publish, zsxq
from content_workbench.config import SecretStore
from content_workbench.xarticle import content_state

from test_app import client, login, save
from test_archive import FakeLark
from test_longform import H, new_longform, png, upload, wait_for


@pytest.fixture(scope="module")
def mp4(tmp_path_factory) -> bytes:
    """One second of 640×360 H.264 with a tone, made by ffmpeg."""
    out = tmp_path_factory.mktemp("clip") / "a.mov"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(out)], check=True, env=clips._env())
    return out.read_bytes()


def put_clip(c, did, data: bytes, piece: int = 50_000, process: bool = True) -> int:
    r = c.post(f"/api/drafts/{did}/clips", json={"name": "录屏.mov", "size": len(data)}, headers=H)
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    for off in range(0, len(data), piece):
        r = c.put(f"/api/clips/{cid}/data?offset={off}", content=data[off:off + piece], headers=H)
        assert r.status_code == 200, r.text
    assert c.put(f"/api/clips/{cid}/data?offset=0", content=data[:piece], headers=H).json()["received"] == len(data)  # resent piece
    assert c.post(f"/api/clips/{cid}/finish", headers=H).json()["state"] == "processing"
    if process:
        clips.process(cid)  # the job, run inline
    return cid


def test_upload_in_pieces_then_play_and_protect(mp4):
    c = client()
    login(c)
    did = new_longform(c)
    assert c.post(f"/api/drafts/{did}/clips", json={"name": "x", "size": clips.MAX_BYTES + 1}, headers=H).status_code == 400
    cid = put_clip(c, did, mp4, process=False)
    assert c.get(f"/api/clips/{cid}").status_code == 404  # not playable yet
    assert c.put(f"/api/clips/{cid}/data?offset=0", content=b"x", headers=H).status_code == 400  # finished uploading
    clips.process(cid)
    info = c.get(f"/api/clips/{cid}/info").json()
    assert info["state"] == "ready" and (info["width"], info["height"]) == (640, 360) and 0.9 < info["duration"] < 1.2
    assert "file" not in info
    r = c.get(f"/api/clips/{cid}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4" and len(r.content) == 100  # browsers seek with ranges
    assert c.get(f"/api/clips/{cid}/poster.jpg").headers["content-type"] == "image/jpeg"
    assert c.get(f"/api/drafts/{did}").json()["clips"][0]["id"] == cid

    save(c, did, {"body": f"看这段\n\n![演示](/api/clips/{cid})"}, headers=H)
    assert c.delete(f"/api/clips/{cid}", headers=H).status_code == 400  # still in the body
    save(c, did, {"body": "删掉了"}, headers=H)
    path = clips.path_of(clips.get(cid))
    assert c.delete(f"/api/clips/{cid}", headers=H).status_code == 200 and not path.exists()


def test_pieces_out_of_order_are_refused(mp4):
    c = client()
    login(c)
    did = new_longform(c)
    cid = c.post(f"/api/drafts/{did}/clips", json={"name": "a.mp4", "size": len(mp4)}, headers=H).json()["id"]
    r = c.put(f"/api/clips/{cid}/data?offset=1000", content=mp4[1000:2000], headers=H)
    assert r.status_code == 400 and "顺序" in r.json()["detail"]
    assert c.post(f"/api/clips/{cid}/finish", headers=H).status_code == 400  # not all there


def test_transcode_when_x_would_refuse_it(tmp_path):
    """A rotated 4K 60 fps phone video with PCM audio comes out upright, inside 1920×1200, AAC."""
    src = tmp_path / "raw.mov"
    rotated = tmp_path / "phone.mov"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=60", "-f", "lavfi",
                    "-i", "sine", "-t", "0.5", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le", str(src)],
                   check=True, env=clips._env())
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-display_rotation:v", "90", "-i", str(src), "-c", "copy", str(rotated)],
                   check=True, env=clips._env())
    info = clips.probe(rotated)
    assert info["rotation"] and not clips.playable(info)
    with db.tx() as conn:
        did = conn.execute("INSERT INTO drafts(platform, created_at, updated_at) VALUES ('wechat', 0, 0)").lastrowid
    data = rotated.read_bytes()
    clip = clips.create(did, "IMG_0001.MOV", len(data))
    clips.append(clip, 0, data)
    clips.finish(clips.get(clip["id"]))
    clips.process(clip["id"])
    out = clips.probe(clips.path_of(clips.get(clip["id"])))
    assert (out["width"], out["height"], out["acodec"], out["rotation"]) == (1080, 1920, "aac", 0)
    assert clips.playable(out)


def test_markdown_helpers():
    body = "a ![x](/api/clips/3)\n\n![](/api/images/4)\n\n![y](/api/clips/5) ![x](/api/clips/3)"
    assert clips.referenced(body) == [3, 5]
    assert clips.as_frames(body) == ("a ![x](/api/clips/3/poster.jpg)\n\n![](/api/images/4)\n\n"
                                     "![y](/api/clips/5/poster.jpg) ![x](/api/clips/3/poster.jpg)")


def test_x_article_video_block():
    md = "开头\n\n![演示](/api/clips/9)\n\n![图](/api/images/2)"
    state = content_state(md, lambda src: ("V9", "tweet_video") if "clips" in src else "I2")
    media = [state["entities"][b["entity_ranges"][0]["key"]]["value"] for b in state["blocks"] if b["type"] == "atomic"]
    assert [m["type"] for m in media] == ["image", "image"]  # X's Articles API has no separate video entity
    assert media[0]["data"] == {"media_items": [{"media_category": "tweet_video", "media_id": "V9"}], "caption": "演示"}
    assert media[1]["data"]["media_items"] == [{"media_category": "tweet_image", "media_id": "I2"}]


def test_push_x_article_with_video_runs_in_background_and_uploads_once(mp4, monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    pic, = upload(c, did, png())
    cid = put_clip(c, did, mp4)
    save(c, did, {"body": f"第一段\n\n![演示](/api/clips/{cid})\n\n![](/api/images/{pic})"}, headers=H)
    videos, got = [], []
    monkeypatch.setattr(publish, "x_upload_image", lambda store, data, mime, name: ("img1", 9e12))

    def upload_video(store, path, progress=None):
        assert path == clips.path_of(clips.get(cid)) and path.read_bytes()[4:8] == b"ftyp"
        progress and progress(1.0)
        videos.append(path)
        return "vid1"
    monkeypatch.setattr(publish, "x_upload_video", upload_video)
    monkeypatch.setattr(publish, "x_article_draft", lambda store, title, state, cover: got.append(state) or {"article_id": f"a{len(got)}"})
    for n in (1, 2):
        r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "x_article"}, headers=H)
        assert r.status_code == 200 and r.json()["pending"], r.text
        p = wait_for(c, r.json()["publication_id"])
        assert (p["state"], p["remote_id"]) == ("sent", f"a{n}")
    assert len(videos) == 1  # the X media id is reused while it is fresh
    items = [e["value"]["data"]["media_items"][0] for e in got[0]["entities"] if e["value"]["type"] == "image"]
    assert items == [{"media_category": "tweet_video", "media_id": "vid1"}, {"media_category": "tweet_image", "media_id": "img1"}]


def test_push_x_article_failure_is_recorded(mp4, monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    cid = put_clip(c, did, mp4)
    save(c, did, {"body": f"![](/api/clips/{cid})"}, headers=H)

    def broken(store, path, progress=None):
        raise publish.PublishError("X 处理视频失败：InvalidMedia")
    monkeypatch.setattr(publish, "x_upload_video", broken)
    r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "x_article"}, headers=H)
    p = wait_for(c, r.json()["publication_id"])
    assert p["state"] == "failed" and "InvalidMedia" in p["detail"]


def test_x_article_refuses_a_video_over_140_seconds_before_uploading(mp4, monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    cid = put_clip(c, did, mp4)
    with db.tx() as conn:
        conn.execute("UPDATE clips SET duration=161.4 WHERE id=?", (cid,))
    save(c, did, {"body": f"第一段\n\n![](/api/clips/{cid})"}, headers=H)
    monkeypatch.setattr(publish, "x_upload_video", lambda *a, **kw: pytest.fail("nothing should be uploaded"))
    r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "x_article"}, headers=H)
    p = wait_for(c, r.json()["publication_id"])
    assert p["state"] == "failed" and "2:20" in p["detail"] and "2:41" in p["detail"]


def test_unready_video_blocks_every_push(mp4):
    c = client()
    login(c)
    did = new_longform(c)
    cid = put_clip(c, did, mp4, process=False)
    save(c, did, {"body": f"![](/api/clips/{cid})"}, headers=H)
    SecretStore().set("wx_default_cover", "C")
    for target in ("x_article", "wechat"):
        r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": target}, headers=H)
        assert r.status_code == 400 and "还在处理" in r.json()["detail"], (target, r.text)


def test_wechat_gets_the_frame_and_a_line_saying_which_file_to_upload(mp4, monkeypatch):
    """An API-uploaded video can't go into a 公众号 article, so nothing goes to the 素材库."""
    c = client()
    login(c)
    did = new_longform(c)
    cid = put_clip(c, did, mp4)
    save(c, did, {"body": f"开头\n\n![演示](/api/clips/{cid})\n\n结尾"}, headers=H)
    SecretStore().set("wx_default_cover", "COVER")
    monkeypatch.setattr(publish, "wx_upload_video", lambda *a, **k: pytest.fail("a 素材库 video can't be used in an article"))
    inline, sent = [], []
    monkeypatch.setattr(publish, "wx_upload_inline", lambda store, data, mime, name: inline.append(data[:3]) or "http://mmbiz.qpic.cn/f/1")
    monkeypatch.setattr(publish, "wx_draft", lambda store, title, md, author, cover, **kw: sent.append(kw["html"]) or {"media_id": "D"})
    for _ in range(2):
        r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "wechat"}, headers=H)
        assert r.status_code == 200 and r.json()["video_frames"] == 1, r.text
    assert inline == [b"\xff\xd8\xff"]  # one JPEG upload, then cached
    html = sent[0]
    assert "/api/clips/" not in html and 'src="https://mmbiz.qpic.cn/f/1"' in html
    note = html.index("这里放视频「录屏.mov」（0:01）")
    assert html.index("mmbiz") < note < html.index("结尾") and "本地上传" in html and "background" in html  # highlighted
    assert "/api/clips/" not in c.post("/api/preview", json={"platform": "wechat", "body": f"![](/api/clips/{cid})"},
                                       headers=H).json()["html"].replace(f"/api/clips/{cid}/poster.jpg", "")


def test_zsxq_gets_the_still_frame_and_a_watch_line(mp4, monkeypatch):
    c = client(zsxq_group_id="123")
    login(c)
    did = new_longform(c)
    cid = put_clip(c, did, mp4)
    save(c, did, {"body": f"开头\n\n![](/api/clips/{cid})"}, headers=H)
    assert c.patch(f"/api/clips/{cid}", json={"link": "bilibili.com/x"}, headers=H).status_code == 400
    assert c.patch(f"/api/clips/{cid}", json={"link": "https://www.bilibili.com/video/BV1"}, headers=H).json()["link"]
    SecretStore().set("zsxq_token", "tok")
    monkeypatch.setattr(zsxq, "upload_image", lambda store, data, mime, name: ("f1", "https://images.zsxq.com/f.jpg"))
    saved = []
    monkeypatch.setattr(zsxq, "call", lambda store, method, path, body=None: saved.append(body["req_data"]) or {"article_id": "art1"})
    r = c.post(f"/api/drafts/{did}/publish", json={"confirm": True, "target": "zsxq"}, headers=H)
    assert r.status_code == 200 and r.json()["video_frames"] == 1, r.text
    md = saved[0]["original_content"]
    assert saved[0]["image_ids"] == ["f1"] and "![](https://images.zsxq.com/f.jpg)" in md
    assert "▶ [看视频（0:01）](https://www.bilibili.com/video/BV1)" in md


def test_feishu_archive_plays_the_video(mp4, monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    cid = put_clip(c, did, mp4)
    save(c, did, {"body": f"- 列表里\n- ![](/api/clips/{cid})\n\n后面"}, headers=H)
    assert ("clip", cid) in feishu.segments(db.row("SELECT body FROM drafts WHERE id=?", (did,))["body"])
    lark = FakeLark()
    seen = []
    original = lark.__call__

    def spy(args, cwd=None, timeout=120):
        if "+media-insert" in args:
            seen.append((args, sorted(p.name for p in cwd.iterdir())))
        return original(args, cwd=cwd, timeout=timeout)
    monkeypatch.setattr(feishu, "_lark", spy)
    assert feishu.sync(did)["state"] == "ok"
    args, files = seen[0]
    assert args[args.index("--type") + 1] == "file" and args[args.index("--file-view") + 1] == "preview"
    assert files == ["录屏.mp4"] and args[args.index("--file") + 1] == "录屏.mp4"
