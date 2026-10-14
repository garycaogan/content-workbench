"""小红书 version: AI rewrite, drawn cards, editing, zip."""

import io
import json
import zipfile

from PIL import Image

from content_workbench import db, feishu, llm, xhs
from content_workbench.config import Settings

from test_app import client, login
from test_longform import H, new_longform, png, upload


def test_generate_render_edit_and_zip(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    pic, = upload(c, did, png())
    c.put(f"/api/drafts/{did}", json={"body": f"第一段讲起因。\n\n![](/api/images/{pic})\n\n第二段讲结果。"}, headers=H)

    def fake(prompt, timeout=600, tools=None, max_turns=2):
        assert f"- {pic}：第一段讲起因。" in prompt
        return json.dumps({"title": "我让 AI 给我搭了个内容工作台，这个标题有点长了", "body": "正文" * 600,
                           "tags": ["#AI工具", "内容创作"], "cover": {"lines": ["说过的话", "自动变草稿"], "highlight": "自动变草稿",
                                                                   "kicker": "引子", "subtitle": "副标题", "pills": ["工作台"]},
                           "cards": [{"title": "要点一", "points": ["一条", "两条"]}, {"title": "要点二", "points": ["三条"]}],
                           "use_images": [pic, 999]})
    monkeypatch.setattr(llm, "run", fake)
    assert c.post(f"/api/drafts/{did}/xhs/generate", headers=H).json()["generating"] is True
    xhs.generate(Settings(), did)
    s = c.get(f"/api/drafts/{did}").json()["xhs"]
    assert len(s["title"]) == 20 and len(s["body"]) == 1000 and s["tags"] == ["AI工具", "内容创作"]
    assert len(s["card_images"]) == 3 and s["extra_images"] == [pic]
    cover = Image.open(io.BytesIO(c.get(f"/api/images/{s['card_images'][0]}").content))
    assert cover.size == (1080, 1440)

    s = c.put(f"/api/drafts/{did}/xhs", json={"title": "短标题", "tags": ["#a", "b"], "extra_images": [pic, 12345],
                                              "cards": [{"title": "只剩一张", "points": ["x"]}]}, headers=H).json()
    assert s["title"] == "短标题" and s["tags"] == ["a", "b"] and s["extra_images"] == [pic]
    old = s["card_images"]
    s = c.post(f"/api/drafts/{did}/xhs/render", headers=H).json()
    assert len(s["card_images"]) == 2 and not set(old) & set(s["card_images"])
    assert c.get(f"/api/images/{old[0]}").status_code == 404  # replaced cards are gone

    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/drafts/{did}/xhs.zip").content))
    assert z.namelist() == ["01.png", "02.png", "03.png", "文案.txt"]
    assert z.read("文案.txt").decode().startswith("短标题") and "#a #b" in z.read("文案.txt").decode()

    segs = feishu.document_segments(db.row("SELECT * FROM drafts WHERE id=?", (did,)))
    assert any("<h3>小红书版</h3>" in str(v) for k, v in segs if k == "xml")
