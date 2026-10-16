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
