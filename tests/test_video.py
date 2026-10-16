"""Short video: script planning, speakable text, timing, captions, compositions, API."""

import json
import re

from content_workbench import llm, video, video_scenes as vs
from content_workbench.config import Settings

from test_app import client, login
from test_longform import H, new_longform, png, upload


def test_numbers_and_names_are_read_out_and_mapped_back():
    assert [video.num2zh(n) for n in (0, 5, 10, 15, 62, 100, 101, 866, 1005, 12000)] == [
        "零", "五", "十", "十五", "六十二", "一百", "一百零一", "八百六十六", "一千零五", "一万二千"]
    text = "让 Claude 在 2026年 看了 866 篇，挑出 62 篇，涨了 35%，网址 github.com/a/b2 不读"
    spoken, segs = video.speakable(text)
    assert "克劳德" in spoken and "二零二六年" in spoken and "八百六十六 篇" in spoken and "百分之三十五" in spoken
    assert "github.com/a/b2" in spoken  # links stay as they are
    assert video.speakable("15:11 提过来，15:36 上线，9:05 开会")[0] == "十五点十一分 提过来，十五点三十六分 上线，九点零五分 开会"
    at = text.index("866")
    assert spoken[video.disp_to_spoken(at, segs):].startswith("八百六十六")
    s0 = spoken.index("八百六十六")
    assert video.disp_span(s0, s0 + 5, segs) == (at, at + 3)  # a replaced number is never cut in half


def test_even_timing_and_captions_without_whisper():
    say = "我这两年在飞书建的 866 篇文档，它挑出 62 篇没发过的。"
    spoken, segs = video.speakable(say)
    times = video.align(spoken, [], 6.0)
    starts = [t[0] for t in times if t]
    assert starts == sorted(starts) and starts[0] >= video.LEAD_S and max(t[1] for t in times if t) <= 6.0
    chunks = video.caption_chunks(say, spoken, segs, times)
    text = "".join(c["text"] for c in chunks)
    assert "866" in text and "62" in text and "八百" not in text
    assert chunks[-1]["phrase_end"] and any(c["phrase_end"] for c in chunks[:-1])
    groups = video.caption_groups(chunks)
    assert all(sum(vs.cells(w["text"]) for w in g["words"]) <= 14 for g in groups)
    cue = video.make_cue(say, segs, times, 0.0, 6.0)
    assert cue("866", 9.0) < cue("62", 9.0) < 6.0 and cue("没有这句", 1.25) == 1.25


def test_whisper_vocabulary_comes_from_settings():
    assert video.vocab_prompt("") == "以下是普通话口播。"
    assert video.vocab_prompt(" 飞书、Claude ") == "以下是普通话口播。常见词：飞书、Claude。"


def test_normalize_keeps_only_known_pictures_and_cues_items():
    s = video.normalize({"type": "shot", "say": "开会、直播都要说", "image": 99, "title": "x" * 80,
                         "items": [{"text": "直播"}]}, {1})
    assert s["type"] == "points" and "image" not in s and len(s["title"]) == 40
    assert s["items"] == [{"text": "直播", "cue": "直播"}]
    s = video.normalize({"type": "shot", "say": "看这里", "image": 1, "focus": [0.2, 0.1, 0.6, 0.5]}, {1})
    assert s["image"] == 1 and s["focus"] == [0.2, 0.1, 0.6, 0.5]
    assert video.normalize({"type": "shot", "say": "还没选图"}, {1}, keep_type=True)["type"] == "shot"
    assert video.normalize({"type": "weird", "say": "a", "value": "共 866 篇"}, set())["value"] == "866"


def _cue(phrase, default):
    return default


def test_every_scene_type_builds_a_registered_composition():
    scenes = [
        {"type": "hook", "say": "a", "lead": "我现在写一篇公众号", "words": ["说话", "和改稿"], "accent": "改稿",
         "chips": [{"text": "找素材"}, {"text": "推到草稿箱", "sub": "公众号 · X"}], "box": "全交给 AI"},
        {"type": "points", "say": "a", "eyebrow": "01", "title": "一个很长很长的标题需要换成两行来放", "items": [{"text": "开会", "sub": "说明"}] * 4, "footer": "底部大字"},
        {"type": "shot", "say": "a", "title": "截图", "image": {"src": "assets/img-1.png", "w": 2880, "h": 1800},
         "focus": [0.2, 0.1, 0.9, 0.2], "label": "标注", "badges": [{"text": "0 行代码"}], "title2": "换一个标题"},
        {"type": "stat", "say": "a", "title": "数字", "value": "866", "unit": "篇", "then": "62", "then_unit": "篇没发过", "sub": "小字"},
        {"type": "change", "say": "a", "title": "改法", "rows": [{"label": "开头", "before": "旧的", "after": "新的"}], "footer": "5 条"},
        {"type": "quote", "say": "a", "lead": "引子", "lines": ["永远相信 AI 比你聪明", "但需求只有你自己清楚"], "accent": "需求"},
        {"type": "cta", "say": "a", "image": {"src": "assets/img-2.png", "w": 1708, "h": 1630}, "badge": "已开源",
         "chips": [{"text": "Claude Code"}], "command": "帮我装 github.com/x/y", "sign": "我是 Gary", "sign_sub": "小字"},
    ]
    for n, scene in enumerate(scenes, 1):
        out = vs.scene_html(f"s{n:02d}", scene, 8.0, _cue, final=(n == len(scenes)))
        assert out.startswith("<template>") and out.rstrip().endswith("</template>")
        assert f'data-composition-id="s{n:02d}"' in out and f'window.__timelines["s{n:02d}"] = tl;' in out
        assert "Math.random" not in out and "repeat" not in out
        # everything is placed inside the stage (above the caption band)
        for top in re.findall(r"top:(\d+)px", out):
            assert int(top) < vs.CAP_TOP, (scene["type"], top)
    index = vs.index_html(scenes, [0, 8, 16, 24, 32, 40, 48], [8.0] * 7, [f"v{n}.wav" for n in range(7)], "bgm.wav", 0.1, 56.0)
    assert index.count('class="scene"') == 9 and 'id="el-bgm"' in index and index.count("tl.fromTo(") == 6


def test_plan_render_guard_and_edit_api(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    pic, = upload(c, did, png())
    c.put(f"/api/drafts/{did}", json={"body": f"第一段讲起因。\n\n![](/api/images/{pic})\n\n第二段讲结果。"}, headers=H)

    def fake(prompt, timeout=600, tools=None, max_turns=2, allow_read=False):
        assert allow_read and tools == ["Read"] and f"编号 {pic}" in prompt and "img-" in prompt
        return json.dumps({"scenes": [
            {"type": "hook", "say": "写公众号只干两件事", "words": ["说话", "改稿"]},
            {"type": "shot", "say": "看这张图", "image": pic, "focus": [0.1, 0.1, 0.5, 0.5], "focus_cue": "这张图"},
            {"type": "shot", "say": "这张图不存在", "image": 4242},
            {"type": "cta", "say": "开源了"},
        ]})
    monkeypatch.setattr(llm, "run", fake)
    assert c.post(f"/api/drafts/{did}/video/plan", headers=H).json()["status"] == "planning"
    video.plan(Settings(author="Gary"), did)
    v = c.get(f"/api/drafts/{did}").json()["video"]
    assert [s["type"] for s in v["scenes"]] == ["hook", "shot", "points", "cta"]
    assert v["scenes"][1]["image"] == pic and v["scenes"][-1]["sign"] == "我是 Gary"

    v = c.put(f"/api/drafts/{did}/video", json={"scenes": v["scenes"][:2] + [{"type": "shot", "say": "新镜头"}], "voice": "nope", "bgm": False},
              headers=H).json()
    assert len(v["scenes"]) == 3 and v["scenes"][2]["type"] == "shot" and v["bgm"] is False and "voice" not in v and v["edited_at"]
    assert c.put(f"/api/drafts/{did}/video", json={"voice": "longanlang_v3.1"}, headers=H).json()["voice"] == "longanlang_v3.1"

    # no TTS key configured: rendering is refused up front, nothing is queued
    r = c.post(f"/api/drafts/{did}/video/render", headers=H)
    assert r.status_code == 400 and "Evolink" in r.json()["detail"]
    assert c.get(f"/api/drafts/{did}/video.mp4").status_code == 404
    assert c.get("/api/video/options").json()["voices"][0]["id"] == "longanyang_v3.1"


def _no_fonts(monkeypatch):
    def fake(text, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        for w in vs.WEIGHTS:
            (out_dir / f"noto-{w}.woff2").write_bytes(b"font")
    monkeypatch.setattr(video, "subset_fonts", fake)


def test_videos_page_lists_long_form_drafts_with_their_state():
    c = client()
    login(c)
    did = new_longform(c, body="正文")
    c.post("/api/drafts", json={"platform": "x", "title": "推文", "body": "x"}, headers=H)
    c.put(f"/api/drafts/{did}/video", json={"scenes": [{"type": "hook", "say": "写公众号只干两件事", "words": ["说话"]}]}, headers=H)
    rows = c.get("/api/videos").json()
    assert [r["id"] for r in rows] == [did]
    assert rows[0]["scenes"] == 1 and rows[0]["has_body"] and rows[0]["status"] == "" and rows[0]["video"] is None


def test_scene_preview_plays_in_the_browser_and_speaks_only_when_asked(monkeypatch):
    _no_fonts(monkeypatch)
    monkeypatch.setattr(video, "whisper_available", lambda: False)
    c = client()
    login(c)
    did = new_longform(c, body="正文")
    pic, = upload(c, did, png())
    scene = {"type": "shot", "say": "看这张图，866 篇里挑出 62 篇", "title": "截图", "image": pic, "focus": [0.1, 0.1, 0.6, 0.5]}

    # no voice yet: silent, timing estimated, nothing sent to the TTS
    def no_tts(*a, **kw):
        raise AssertionError("preview must not synthesise unless asked")
    monkeypatch.setattr(video, "synth", no_tts)
    p = c.post(f"/api/drafts/{did}/video/preview", json={"index": 1, "scene": scene}, headers=H).json()
    assert p["voiced"] is False and p["audio"] is None and p["duration"] > 2
    page = c.get(p["url"])
    assert page.status_code == 200 and page.headers["x-frame-options"] == "SAMEORIGIN"
    html = page.text
    assert 'data-composition-id="s02"' in html and 'window.__timelines["captions"]' in html and "window.cwSeek" in html
    assert "<template>" not in html and html.count("gsap.min.js") == 1
    base = p["url"].rsplit("/", 1)[0]
    assert c.get(f"{base}/assets/img-{pic}.png").status_code == 200 and c.get(f"{base}/assets/fonts/noto-700.woff2").status_code == 200
    assert c.get(f"{base}/../../video.mp4").status_code == 404 and c.get(f"/api/drafts/{did}/video/preview/zzz/index.html").status_code == 404
    assert c.get("/api/videos").headers["x-frame-options"] == "DENY"

    # asking to hear it needs a key, then makes the voice once and keeps it
    assert "Evolink" in c.post(f"/api/drafts/{did}/video/preview", json={"index": 1, "scene": scene, "speak": True}, headers=H).json()["detail"]
    import wave
    made = []

    def fake_synth(key, text, voice, out):
        made.append(text)
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\0\0" * 24000 * 3)
    monkeypatch.setattr(video, "synth", fake_synth)
    monkeypatch.setattr(video, "evolink_key", lambda s: "k")
    p2 = c.post(f"/api/drafts/{did}/video/preview", json={"index": 1, "scene": scene, "speak": True}, headers=H).json()
    assert p2["voiced"] and p2["audio"] and p2["url"] != p["url"] and 3.0 < p2["duration"] < 4.0
    assert made == ["看这张图，八百六十六 篇里挑出 六十二 篇"]
    assert c.get(p2["audio"]).status_code == 200
    # the same scene again: same page, no new voice; a visual edit reuses the voice
    assert c.post(f"/api/drafts/{did}/video/preview", json={"index": 1, "scene": scene}, headers=H).json()["url"] == p2["url"]
    p3 = c.post(f"/api/drafts/{did}/video/preview", json={"index": 1, "scene": {**scene, "title": "换个标题"}}, headers=H).json()
    assert p3["voiced"] and p3["url"] != p2["url"] and len(made) == 1
    assert c.post(f"/api/drafts/{did}/video/preview", json={"index": 0, "scene": {"type": "hook", "say": " "}}, headers=H).status_code == 400


def test_own_voice_is_cloned_from_a_recording_and_recloned_when_it_expires(monkeypatch, tmp_path):
    import wave
    ref = tmp_path / "me.wav"
    with wave.open(str(ref), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b"\1\0" * 16000)
    s = Settings(video_clone_ref=str(ref), video_clone_name="我的声音", video_voice="clone")
    assert [v["id"] for v in video.voices(s)][:2] == ["clone", "longanyang_v3.1"] and video.voices(s)[0]["name"] == "我的声音"
    assert video.default_voice(s) == "clone" and video.default_voice(Settings(video_voice="clone")) == "longanyang_v3.1"
    assert video.draft_voice(s, {"voice": "gone"}) == "clone" and video.draft_voice(s, {"voice": "longanlang_v3.1"}) == "longanlang_v3.1"

    uploads, enrolls, said = [], [], []
    monkeypatch.setattr(video, "_upload", lambda key, path: uploads.append(path) or f"https://files/{len(uploads)}")

    def fake_ev(key, method, path, body=None):
        enrolls.append(body["audio_url"])
        return {"id": "t"}
    monkeypatch.setattr(video, "_ev", fake_ev)
    monkeypatch.setattr(video, "_task", lambda key, task, what, tries=150: {"result_data": {"voice": f"v{len(enrolls)}"}})
    assert video.cloned_voice(s, "k") == "v1" and video.cloned_voice(s, "k") == "v1"  # kept while fresh
    state = video.data_dir() / "video" / "clone.json"
    st = json.loads(state.read_text())
    st["voice_at"] -= video.CLONE_TTL + 1
    state.write_text(json.dumps(st))
    assert video.cloned_voice(s, "k") == "v2" and uploads == [ref] and enrolls == ["https://files/1"] * 2  # link reused

    # the TTS says the clone has expired before its time: clone again and retry once
    def fake_synth(key, text, voice, out, language=""):
        said.append((voice, language))
        if voice == "v2":
            raise video.VoiceExpired("expired")
        out.write_bytes(b"wav")
    monkeypatch.setattr(video, "synth", fake_synth)
    video.speak(s, "k", "一句话", "clone", tmp_path / "out.wav")
    assert said == [("v2", "zh"), ("v3", "zh")] and (tmp_path / "out.wav").read_bytes() == b"wav"
    video.speak(s, "k", "一句话", "longanlang_v3.1", tmp_path / "p.wav")
    assert said[-1] == ("longanlang_v3.1", "")

    # the cache follows the recording, not the clone's changing id
    key1 = video.voice_key(s, "clone")
    assert key1.startswith("clone-") and video.voice_key(s, "longanlang_v3.1") == "longanlang_v3.1"
    with wave.open(str(ref), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b"\2\0" * 16000)
    import os
    os.utime(ref, (1, 1))
    assert video.voice_key(s, "clone") != key1
    assert video.cloned_voice(s, "k") == "v4" and len(uploads) == 2  # a new recording is uploaded and cloned


def test_own_voice_is_offered_by_the_api_when_configured(tmp_path):
    ref = tmp_path / "me.wav"
    ref.write_bytes(b"RIFF")
    c = client(video_clone_ref=str(ref), video_voice="clone")
    login(c)
    o = c.get("/api/video/options").json()
    assert o["voices"][0]["id"] == "clone" and o["default_voice"] == "clone"
    did = new_longform(c)
    assert c.put(f"/api/drafts/{did}/video", json={"voice": "clone"}, headers=H).json()["voice"] == "clone"
