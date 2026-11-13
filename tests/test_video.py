"""Short video: script planning, speakable text, timing, captions, compositions, API."""

import json
import re
import threading
import time

from content_workbench import clips, llm, video, video_scenes as vs
from content_workbench.config import Settings

from test_app import client, login, save
from test_clips import mp4, put_clip  # noqa: F401  (a one-second clip and its upload)
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
        {"type": "clip", "say": "a", "eyebrow": "案例", "title": "水果演短剧", "accent": "短剧", "label": "TikTok · 2569万播放",
         "source": "@x · TikTok", "badges": [{"text": "21.6万粉"}], "clip": {"src": "assets/clip-a.mp4", "w": 1080, "h": 1920, "fill": "full"}},
        {"type": "clip", "say": "a", "title": "原片", "label": "标注", "source": "@x", "clip": {"src": "assets/clip-b.mp4", "w": 1280, "h": 720, "fill": "window"}},
        {"type": "cta", "say": "a", "image": {"src": "assets/img-2.png", "w": 1708, "h": 1630}, "badge": "已开源",
         "chips": [{"text": "Claude Code"}], "command": "帮我装 github.com/x/y", "sign": "我是 Gary", "sign_sub": "小字"},
    ]
    for frame in (vs.VERTICAL, vs.LANDSCAPE):
        for style in vs.STYLES.values():
            for n, scene in enumerate(scenes, 1):
                out = vs.scene_html(f"s{n:02d}", scene, 8.0, _cue, final=(n == len(scenes)), frame=frame, style=style)
                assert out.startswith("<template>") and out.rstrip().endswith("</template>")
                assert f'data-composition-id="s{n:02d}"' in out and f'window.__timelines["s{n:02d}"] = tl;' in out
                assert "Math.random" not in out and "repeat" not in out
                if scene["type"] == "clip":  # HyperFrames plays it: muted, timed from the scene's start, never cross-origin
                    assert re.search(rf'<video id="s{n:02d}-video" class="clip" src="assets/clip-\w\.mp4" muted playsinline preload="auto" '
                                     r'data-start="0" data-duration="8.0"', out) and "crossorigin" not in out and ".play(" not in out
                # everything is placed inside the stage (above the caption band, inside the width)
                for top in re.findall(r"top:(\d+)px", out):
                    assert int(top) < frame.CAP_TOP, (frame.key, style.key, scene["type"], top)
                for left in re.findall(r"left:(\d+)px", out):
                    assert int(left) < frame.W, (frame.key, style.key, scene["type"], left)
    index = vs.index_html(scenes, [0, 8, 16, 24, 32, 40, 48], [8.0] * 7, [f"v{n}.wav" for n in range(7)], "bgm.wav", 0.1, 56.0)
    assert index.count('class="scene"') == 9 and 'id="el-bgm"' in index and index.count("tl.fromTo(") == 6


def test_clip_scenes_keep_only_the_drafts_ready_videos_and_a_sane_stretch():
    ok = {7: 12.0}
    s = video.normalize({"type": "clip", "say": "看原片", "clip": "7", "clip_start": "2.04", "clip_end": 5, "fill": "full", "sound": True,
                         "source": "@账号 · TikTok"}, set(), allowed_clips=ok)
    assert (s["clip"], s["clip_start"], s["clip_end"], s["fill"], s["sound"], s["source"]) == (7, 2.0, 5.0, "full", True, "@账号 · TikTok")
    s = video.normalize({"type": "clip", "say": "a", "clip": 7, "clip_start": 40, "clip_end": 1, "fill": "odd", "sound": "yes"}, set(), allowed_clips=ok)
    assert s["clip_start"] == 11.5 and not {"clip_end", "fill", "sound"} & set(s)
    assert video.normalize({"type": "clip", "say": "a", "clip": 8}, set(), allowed_clips=ok)["type"] == "points"  # not this draft's
    assert video.normalize({"type": "clip", "say": "a", "clip": 7}, set())["type"] == "points"  # no videos offered
    kept = video.normalize({"type": "clip", "say": "还没选视频"}, set(), keep_type=True, allowed_clips=ok)
    assert kept["type"] == "clip" and "clip" not in kept
    assert video.clip_take({"clip_start": 2.0}, 12.0) == (2.0, 10.0) and video.clip_take({"clip_start": 1, "clip_end": 3}, 12.0) == (1, 2.0)
    assert video.clip_take({}, 90.0) == (0.0, video.CLIP_HOLD)  # never longer than a scene can be
    # the clip fills the canvas when it has its shape, otherwise it sits in a frame
    assert vs.clip_fill(None, 576, 1024, vs.VERTICAL) == "full" and vs.clip_fill(None, 576, 864, vs.VERTICAL) == "full"
    assert vs.clip_fill(None, 1920, 1080, vs.VERTICAL) == "window" and vs.clip_fill(None, 1920, 1080, vs.LANDSCAPE) == "full"
    assert vs.clip_fill("window", 576, 1024, vs.VERTICAL) == "window"


def test_plan_render_guard_and_edit_api(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    pic, = upload(c, did, png())
    save(c, did, {"body": f"第一段讲起因。\n\n![](/api/images/{pic})\n\n第二段讲结果。"}, headers=H)

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
    def fake(text, out_dir, faces=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        for face in faces or vs.DEFAULT.faces:
            for w in face.weights:
                (out_dir / f"{face.stem}-{w}.woff2").write_bytes(b"font")
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


def test_a_body_video_is_offered_to_the_planner_and_plays_in_preview_and_render(monkeypatch, mp4):  # noqa: F811
    import wave
    _no_fonts(monkeypatch)
    monkeypatch.setattr(video, "whisper_available", lambda: False)
    c = client()
    login(c)
    did = new_longform(c)
    cid = put_clip(c, did, mp4)
    save(c, did, {"body": f"先看原片。\n\n![原片开头](/api/clips/{cid})\n\n再讲做法。"}, headers=H)

    def fake(prompt, timeout=600, tools=None, max_turns=2, allow_read=False):
        assert f"编号 {cid}（640×360" in prompt and f"clip-{cid}.jpg" in prompt and "原片开头" in prompt and max_turns == 5
        return json.dumps({"scenes": [{"type": "clip", "say": "先看原片", "clip": cid, "title": "原片", "clip_end": 0.6},
                                      {"type": "clip", "say": "这段不存在", "clip": cid + 99}]})
    monkeypatch.setattr(llm, "run", fake)
    video.plan(Settings(), did)
    scenes = c.get(f"/api/drafts/{did}").json()["video"]["scenes"]
    assert scenes[0]["clip"] == cid and scenes[0]["clip_end"] == 0.6 and scenes[1]["type"] == "points"

    # the preview plays the cut the render uses: in a frame on the vertical canvas, over the whole landscape one
    scene = {**scenes[0], "sound": True}
    for fmt, size in (("vertical", (640, 360)), ("landscape", (1920, 1080))):
        p = c.post(f"/api/drafts/{did}/video/preview", json={"index": 0, "scene": scene, "format": fmt}, headers=H).json()
        html = c.get(p["url"]).text
        src = re.search(r'<video id="s01-video" class="clip" src="(assets/clip-\w+\.mp4)" muted', html).group(1)
        base = p["url"].rsplit("/", 1)[0]
        assert c.get(f"{base}/{src}", headers={"Range": "bytes=0-99"}).status_code == 206  # the browser seeks it with ranges
        assert '<audio id="clip-sound" src="assets/audio/clip.wav"' in html and "follow(t)" in html
        built = video.preview_dir(did) / p["url"].split("/")[-2]
        cut = clips.probe(built / src)
        assert (cut["width"], cut["height"]) == size and cut["acodec"] is None and abs(cut["duration"] - video.CLIP_HOLD) < 0.2
        sound = built / "assets" / "audio" / "clip.wav"
        assert 0.5 < video.duration_of(sound) < 0.7 and video.CLIP_LUFS - 6 < video.loudness(sound) < video.CLIP_LUFS + 1

    # the render: the cut in the scene's composition, its sound laid under the voice at the scene's start
    def fake_speak(settings, key, text, voice, out):
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\0\0" * 36000)
    monkeypatch.setattr(video, "speak", fake_speak)
    monkeypatch.setattr(video, "evolink_key", lambda s: "k")
    c.put(f"/api/drafts/{did}/video", json={"scenes": [{"type": "quote", "say": "先说一句", "lines": ["先说"]}, scene]}, headers=H)
    root, lines, starts, _ = video.build_project(Settings(), did)
    comp = (root / "compositions" / "s02.html").read_text(encoding="utf-8")
    src = re.search(r'<video id="s02-video" class="clip" src="(assets/clip-\w+\.mp4)"', comp).group(1)
    assert (root / src).is_file() and "<video" not in (root / "compositions" / "s01.html").read_text(encoding="utf-8")
    index = (root / "index.html").read_text(encoding="utf-8")
    assert f'<audio id="el-clip-sound-1" src="assets/audio/clip-02.wav" data-start="{starts[1]:.3f}" data-duration="0.600"' in index
    assert (root / "assets" / "audio" / "clip-02.wav").is_file()


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


def test_own_voice_on_minimax_is_read_there_and_never_falls_back_to_evolink(monkeypatch, tmp_path):
    import io
    import urllib.request
    import pytest
    keyf = tmp_path / "mm.key"
    keyf.write_text("mm-secret\n")
    s = Settings(minimax_voice_id="Me01", minimax_key_file=str(keyf), minimax_speed=1.1, video_voice="clone",
                 video_clone_ref=str(tmp_path / "old.wav"))  # an old Evolink recording setting does not matter
    assert video.voices(s)[0]["id"] == "clone" and "MiniMax" in video.voices(s)[0]["desc"] and video.default_voice(s) == "clone"
    assert video.voice_key(s, "clone") == "minimax-Me01-speech-2.8-hd-1.1"
    assert video.tts_key(s, "clone") == ""  # no Evolink key needed for the author's voice
    assert video.options(s)["tts"] is True

    def no_evolink(*a, **k):
        raise AssertionError("went to Evolink")
    monkeypatch.setattr(video, "synth", no_evolink)
    monkeypatch.setattr(video, "cloned_voice", no_evolink)
    monkeypatch.setattr(video.time, "sleep", lambda s: None)
    sent, replies = [], []

    def fake_open(req, timeout=0):
        sent.append((req.full_url, req.headers.get("Authorization"), json.loads(req.data)))
        return io.BytesIO(json.dumps(replies.pop(0)).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)

    ok = {"base_resp": {"status_code": 0, "status_msg": "success"}, "data": {"audio": b"RIFFwav".hex()}}
    replies[:] = [{"base_resp": {"status_code": 1002, "status_msg": "rate limit"}}, ok]  # a rate limit is tried again
    video.speak(s, "", "一句话", "clone", tmp_path / "out.wav")
    assert (tmp_path / "out.wav").read_bytes() == b"RIFFwav" and len(sent) == 2
    url, auth, body = sent[-1]
    assert url == "https://api.minimaxi.com/v1/t2a_v2" and auth == "Bearer mm-secret"
    assert body["model"] == "speech-2.8-hd" and body["text"] == "一句话" and body["voice_setting"]["voice_id"] == "Me01"
    assert body["voice_setting"]["speed"] == 1.1 and body["audio_setting"]["format"] == "wav"

    # a failure says why and stops; nothing is read by another voice
    sent.clear()
    replies[:] = [{"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}]
    with pytest.raises(video.VideoError, match="1008.*余额不足.*没有换成别的音色"):
        video.speak(s, "", "一句话", "clone", tmp_path / "x.wav")
    assert len(sent) == 1 and not (tmp_path / "x.wav").exists()

    # no key file: refused before anything is built, still without Evolink
    s.minimax_key_file = str(tmp_path / "missing.key")
    with pytest.raises(video.VideoError, match="minimax_key_file"):
        video.tts_key(s, "clone")
    # presets still use the Evolink key; without MiniMax settings the old clone path is unchanged
    monkeypatch.setattr(video, "evolink_key", lambda st: "ev")
    assert video.tts_key(s, "longanyang_v3.1") == "ev" and video.tts_key(Settings(), "clone") == "ev"
    assert video.voices(Settings()) == video.VOICES


def test_planner_picks_a_style_and_the_author_can_switch_it_and_render_landscape(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c, body="正文")

    def fake(prompt, timeout=600, tools=None, max_turns=2, allow_read=False):
        assert "news（头条快剪）" in prompt and "paper（暖白杂志）" in prompt and "横屏" in prompt and "keys" in prompt
        return json.dumps({"style": "news", "headline": ["Opus 5.5 来了", "强在哪？"], "headline_key": "Opus",
                           "scenes": [{"type": "hook", "say": "新模型来了，强了三倍", "words": ["新模型"], "keys": ["三倍", "没说过"]},
                                      {"type": "points", "say": "两件事", "items": [{"text": "快", "icon": "⚡️"}]}]})
    monkeypatch.setattr(llm, "run", fake)
    video.plan(Settings(), did)
    v = c.get(f"/api/drafts/{did}").json()["video"]
    assert v["style"] == "news" and v["headline"] == ["Opus 5.5 来了", "强在哪？"] and v["headline_key"] == "Opus"
    assert v["scenes"][0]["keys"] == ["三倍"] and v["scenes"][1]["items"][0]["icon"] == "⚡️"  # a key word must be said
    assert c.put(f"/api/drafts/{did}/video", json={"style": "nope"}, headers=H).json()["style"] == "news"
    assert c.put(f"/api/drafts/{did}/video", json={"style": "paper", "headline": ["一行"]}, headers=H).json()["style"] == "paper"
    o = c.get("/api/video/options").json()
    assert [s["id"] for s in o["styles"]][:1] == ["keynote"] and {f["id"] for f in o["formats"]} == {"vertical", "landscape"}

    # a landscape render is queued with its format and lands beside the vertical one
    monkeypatch.setattr(video, "evolink_key", lambda s: "k")
    assert c.post(f"/api/drafts/{did}/video/render?format=square", headers=H).status_code == 400
    v = c.post(f"/api/drafts/{did}/video/render?format=landscape", headers=H).json()
    assert v["status"] == "rendering" and v["rendering"] == "landscape"
    from content_workbench import db
    job = db.row("SELECT * FROM jobs WHERE kind='video_render' ORDER BY id DESC LIMIT 1")
    assert json.loads(job["payload"])["format"] == "landscape"
    assert video.video_path(did, "landscape").name == f"draft-{did}-landscape.mp4" and video.render_key("landscape") == "video_landscape"


def test_style_css_never_breaks_an_inline_style_attribute():
    scene = {"type": "hook", "say": "a", "words": ["说话", "和改稿"], "accent": "改稿", "lead": "引子"}
    for style in vs.STYLES.values():
        out = vs.scene_html("s01", scene, 8.0, _cue, style=style)
        for attr in re.findall(r'style="([^"]*)"', out):
            assert "font-family" not in attr or "'" in attr, (style.key, attr)
        if style.display:
            assert f"font-family: '{style.display}'" in out


def test_captions_colour_key_words_and_chrome_keeps_headline_and_progress():
    chunks = video.mark_keys([{"text": "三天下来", "start": 0.2, "end": 0.9, "phrase_end": False},
                              {"text": "就是现在", "start": 1.0, "end": 1.6, "phrase_end": True},
                              {"text": "866 篇", "start": 1.7, "end": 2.2, "phrase_end": True}], ["三天"])
    assert [c["key"] for c in chunks] == [True, False, True]  # a key word, a plain one, a number
    groups = video.caption_groups(chunks, vs.VERTICAL.CAP_CELLS)
    cap = vs.captions_html(groups, 3.0, vs.VERTICAL, vs.STYLES["keynote"])
    assert cap.count("cap-k") >= 2 and "backgroundColor" not in cap and 'opacity: 0, y: 8' in cap

    news = vs.STYLES["news"]
    chrome = vs.chrome_html("作者", "", 30.0, vs.VERTICAL, news, headline=["AI 搭的工作台", "说完就有稿"], head_keys=["工作台"],
                            marks=[0.25, 0.5, 0.75])
    assert 'class="hd hd1"' in chrome and '<span class="k">工作台</span>' in chrome and "chrome-mark" not in chrome
    assert chrome.count("width:4px; height:8px") == 3 and "chrome-fill" in chrome
    # the stage moves down under a standing headline (vertical only)
    out = vs.scene_html("s01", {"type": "points", "say": "a", "title": "标题", "items": [{"text": "一", "icon": "🔥"}]}, 8.0, _cue, style=news)
    tops = [int(x) for x in re.findall(r"top:(\d+)px", out)]
    assert min(tops) >= vs.VERTICAL.TOP + vs.HEADLINE_ROOM and "🔥" in out
    plain = vs.chrome_html("作者", "一行小字", 30.0, vs.LANDSCAPE, vs.STYLES["keynote"])
    assert "chrome-mark" in plain and "一行小字" in plain


def test_screenshots_are_framed_by_kind():
    wide = {"type": "shot", "say": "a", "title": "t", "image": {"src": "assets/a.png", "w": 2880, "h": 1800}, "focus": [0.2, 0.2, 0.5, 0.4]}
    tall = {**wide, "image": {"src": "assets/b.png", "w": 1170, "h": 2532}}
    browser = vs.scene_html("s01", wide, 8.0, _cue, style=vs.STYLES["keynote"])
    assert "#ff5f57" in browser and 'id="s01-view"' in browser and 'id="s01-ring"' in browser
    card = vs.scene_html("s01", wide, 8.0, _cue, style=vs.STYLES["news"])
    assert "#ff5f57" not in card and "#ff3b30" in card  # a plain card, the red box
    phone = vs.scene_html("s01", tall, 8.0, _cue, style=vs.STYLES["keynote"])
    assert "border-radius:56px" in phone


def _rendered(did):
    video.update(did, video={"duration": 3.0, "size": 4, "rendered_at": 1.0, "scenes": []})
    path = video.video_path(did)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mp4!")


def _settled(c, did):
    for _ in range(200):
        rows = c.get(f"/api/drafts/{did}/video/publications").json()
        if rows and all(r["state"] != "pending" for r in rows):
            return rows
        time.sleep(0.02)
    raise AssertionError("send never finished")


def test_a_render_goes_to_x_as_a_post_and_to_the_wechat_library(monkeypatch):
    from content_workbench import publish
    from content_workbench.config import SecretStore
    c = client()
    login(c)
    did = new_longform(c, "正文第一段")
    body = {"target": "x", "format": "vertical", "text": "一句话", "confirm": True}
    assert c.post(f"/api/drafts/{did}/video/publish", json={**body, "confirm": False}, headers=H).status_code == 400
    assert "X 还没连接" in c.post(f"/api/drafts/{did}/video/publish", json=body, headers=H).json()["detail"]
    store = SecretStore()
    store.set("x_tokens", {"access_token": "t", "refresh_token": "r", "expires_at": 9e12})
    store.set("wx_appid", "a")
    store.set("wx_secret", "s")
    assert "还没出竖屏片" in c.post(f"/api/drafts/{did}/video/publish", json=body, headers=H).json()["detail"]
    assert c.post(f"/api/drafts/{did}/video/publish", json={**body, "text": "字" * 141}, headers=H).status_code == 400
    _rendered(did)

    gate = threading.Event()
    calls = []
    monkeypatch.setattr(publish, "x_upload_video", lambda store, path, progress=None: (gate.wait(5), calls.append(path.name))[1] or "m1")
    monkeypatch.setattr(publish, "x_post_video", lambda store, text, media: calls.append((text, media)) or {"ids": ["99"], "url": "u"})
    r = c.post(f"/api/drafts/{did}/video/publish", json=body, headers=H)
    assert r.status_code == 200 and r.json()["state"] == "pending"
    assert "还没发完" in c.post(f"/api/drafts/{did}/video/publish", json=body, headers=H).json()["detail"]
    gate.set()
    sent = _settled(c, did)[0]
    assert (sent["platform"], sent["state"], sent["remote_id"]) == ("x_video", "sent", "99")
    assert calls == ["draft-%d.mp4" % did, ("一句话", "m1")] and json.loads(sent["detail"])["format"] == "vertical"

    got, made = {}, []
    monkeypatch.setattr(video, "shrink", lambda src, fmt, cap: src)
    monkeypatch.setattr(publish, "wx_upload_video", lambda store, path, title, intro: got.update(title=title, intro=intro) or "wxm")
    monkeypatch.setattr(publish, "wx_draft", lambda store, title, md, author, thumb, **k: made.append((title, md, thumb)) or {"media_id": "d1"})
    store.set("wx_default_cover", "cov")
    c.post(f"/api/drafts/{did}/video/publish", json={**body, "target": "wechat"}, headers=H)
    wx = _settled(c, did)[0]
    assert (wx["platform"], wx["state"], wx["remote_id"]) == ("wechat_video", "sent", "wxm")
    assert got == {"title": "长文", "intro": "正文第一段"} and made == [("长文", "正文第一段", "cov")]
    # with a title, intro and cover written: they go along, and the same render is not uploaded twice
    c.put(f"/api/drafts/{did}/video", json={"meta": {"title": "视频标题", "intro": "一段简介", "cover": {"lines": ["大字"], "key": "大"}}}, headers=H)
    monkeypatch.setattr(video, "cover", lambda settings, d, fmt: (video.data_dir() / f"c-{fmt}.jpg").write_bytes(b"jpg") and video.data_dir() / f"c-{fmt}.jpg")
    monkeypatch.setattr(publish, "wx_upload_material", lambda store, data, mime, name: "thumb-" + name)
    c.post(f"/api/drafts/{did}/video/publish", json={**body, "target": "wechat"}, headers=H)
    again = _settled(c, did)[0]
    assert again["state"] == "sent" and made[-1] == ("视频标题", "一段简介", "thumb-c-landscape.jpg") and got["title"] == "长文"
    covers = []
    monkeypatch.setattr(publish, "x_upload_video", lambda store, path, progress=None: "m2")
    monkeypatch.setattr(publish, "x_upload_image", lambda store, data, mime, name: ("img1", 9e12))
    monkeypatch.setattr(publish, "x_set_video_cover", lambda store, media, image: covers.append((media, image)))
    c.post(f"/api/drafts/{did}/video/publish", json=body, headers=H)
    assert _settled(c, did)[0]["state"] == "sent" and covers == [("m2", "img1")]

    def cut_off(*a):
        raise publish.PublishError("发帖时网络中断", uncertain=True)
    monkeypatch.setattr(publish, "x_post_video", cut_off)
    c.post(f"/api/drafts/{did}/video/publish", json=body, headers=H)
    assert _settled(c, did)[0]["state"] == "unknown"


def test_sends_cut_off_by_a_restart_are_closed():
    from content_workbench import db
    with db.tx() as conn:
        for stage in ("upload", "posting"):
            conn.execute("INSERT INTO publications(draft_id, platform, state, detail, created_at) VALUES (NULL,'x_video','pending',?,0)",
                         (json.dumps({"stage": stage}),))
    video.recover_sends()
    states = [(r["state"], "可以重新发" in r["detail"]) for r in db.rows("SELECT * FROM publications ORDER BY id")]
    assert states == [("failed", True), ("unknown", False)]


def test_x_video_upload_is_chunked_and_waits_for_processing(monkeypatch, tmp_path):
    from content_workbench import publish
    from content_workbench.config import SecretStore
    store = SecretStore()
    store.set("x_tokens", {"access_token": "t", "refresh_token": "r", "expires_at": 9e12, "scope": publish.X_SCOPES})
    clip = tmp_path / "v.mp4"
    clip.write_bytes(b"0" * (publish.X_VIDEO_CHUNK * 2 + 10))

    class Resp:
        def __init__(self, data=None, status=200):
            self.status_code, self._data = status, data
            self.content = b"" if data is None else b"{}"
            self.text = ""

        def json(self):
            if self._data is None:
                raise ValueError
            return {"data": self._data}

    seen = []
    status = iter([{"processing_info": {"state": "in_progress", "check_after_secs": 1}}, {"processing_info": {"state": "succeeded"}}])

    def post(url, **k):
        seen.append(url.rsplit("/", 1)[-1] + (k["data"]["segment_index"] if "data" in k else ""))
        if url.endswith("initialize"):
            assert k["json"]["media_category"] == "tweet_video" and k["json"]["total_bytes"] == clip.stat().st_size
            return Resp({"id": "77"})
        if url.endswith("finalize"):
            return Resp({"id": "77", "processing_info": {"state": "pending", "check_after_secs": 1}})
        return Resp(None, 204)

    monkeypatch.setattr(publish.httpx, "post", post)
    monkeypatch.setattr(publish.httpx, "get", lambda url, **k: seen.append(k["params"]["command"]) or Resp(next(status)))
    monkeypatch.setattr(publish.time, "sleep", lambda s: None)
    done = []
    assert publish.x_upload_video(store, clip, done.append) == "77"
    assert seen == ["initialize", "append0", "append1", "append2", "finalize", "STATUS", "STATUS"]
    assert done[-1] == 1.0


def test_title_intro_and_cover_words_are_written_then_edited(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c, "正文")
    pic, = upload(c, did, png())
    c.put(f"/api/drafts/{did}/video", json={"scenes": [{"type": "shot", "say": "看这里", "title": "t", "image": pic}]}, headers=H)
    reply = {"title": "我用 AI 搭了个工作台" + "很长" * 20, "intro": "说一段话。", "cover": {"lines": ["说完话", "稿子就有了"], "key": "稿子", "kicker": "已开源"}}
    monkeypatch.setattr(llm, "run", lambda prompt, **k: "<长文正文（节选）>" in prompt and "看这里" in prompt and json.dumps(reply, ensure_ascii=False))
    drawn = []
    monkeypatch.setattr(video, "cover", lambda settings, d, fmt: drawn.append(fmt))
    meta = video.write_meta(Settings(), did)["meta"]
    assert drawn == ["vertical", "landscape"]
    assert len(meta["title"]) == video.META_TITLE_MAX and meta["cover"] == {"lines": ["说完话", "稿子就有了"], "key": "稿子", "kicker": "已开源", "image": pic}
    r = c.put(f"/api/drafts/{did}/video", json={"meta": {"intro": "改过的简介", "cover": {"key": "不在里面", "image": 99999}}}, headers=H).json()["meta"]
    assert r["intro"] == "改过的简介" and r["title"] == meta["title"] and r["cover"]["key"] == "" and r["cover"]["image"] is None
    assert r["cover"]["lines"] == ["说完话", "稿子就有了"] and r["edited_at"] >= meta["edited_at"]
    monkeypatch.undo()
    assert c.get(f"/api/drafts/{new_longform(c)}/video/cover.jpg").status_code == 404  # nothing written yet


def test_cover_keeps_its_words_inside_the_crops():
    img = {"src": "assets/a.png", "w": 2880, "h": 1800}
    for style in vs.STYLES.values():
        for frame in (vs.VERTICAL, vs.LANDSCAPE):
            for image in (img, None):
                out = vs.cover_html(["说完话", "稿子就有了"], "稿子", "已开源", "作者", "小字", frame, style, image)
                tops = [int(x) for x in re.findall(r"right:0; top:(\d+)px", out)]
                sizes = [int(x) for x in re.findall(r"font-size:(\d+)px; line-height:1.14", out)]
                assert sizes and all(s * 5 <= (1000 if frame.wide else 960) for s in sizes), (style.key, frame.key)
                lo, hi = ((1080 - 817) // 2, (1080 + 817) // 2) if frame.wide else (240, 1680)  # 2.35:1 / 3:4 from the middle
                assert all(lo <= t and t + sizes[0] * 1.14 <= hi for t in tops), (style.key, frame.key, tops)
                assert style.mark_word("稿子") in out and ("a.png" in out) == bool(image)
