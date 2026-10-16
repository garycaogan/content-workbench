"""Prompt builders. Everything about the author (name, identity, how others
address them, how they write on X) comes from ``Settings.persona``; their style
guide, identity document and optional skill scripts come from configured paths,
so the same rules can be shared with other agents that write for them."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from .config import Settings

SKILL_REL = ".agents/skills/ip-content"


PERSONA_DEFAULTS = {
    "name": "",
    "identity": "",
    "speaker_hint": "比如别人直接叫他的名字",
    "table_rule": "- 表格素材里混着别人的内容。只有能看出是 {name} 本人写的部分才算他的原话，其他只当背景。",
    "manual_title": "身份说明",
    "x_style": "他在 X 上写得比长文更随意：短段落，一两句一段。长串第一条一句话点题，末尾加 🧵，后面每条讲一个具体的事。",
    "domain": "按内容选",
}


def persona(settings: Settings) -> dict:
    p = {**PERSONA_DEFAULTS, **{k: v for k, v in (settings.persona or {}).items() if v}}
    p["name"] = p["name"] or settings.author or "作者"
    p["identity_paren"] = f"（{p['identity']}）" if p["identity"] else ""
    p["table_rule"] = p["table_rule"].replace("{name}", p["name"])
    return p


def skill_dir(settings: Settings) -> Path | None:
    if settings.skill_dir:
        return Path(settings.skill_dir).expanduser()
    if settings.workspace_root:
        return Path(settings.workspace_root).expanduser() / SKILL_REL
    return None


def voice_guide(settings: Settings) -> str:
    if settings.voice_guide_path:
        path = Path(settings.voice_guide_path).expanduser()
    else:
        found = sorted((skill_dir(settings) or Path("/nonexistent")).glob("references/*voice*.md"))
        path = found[0] if found else Path("/nonexistent")
    return path.read_text(encoding="utf-8") if path.exists() else ""


def boss_manual(settings: Settings) -> str:
    path = Path(settings.manual_path).expanduser() if settings.manual_path else \
        Path(settings.kb_root).expanduser() / "1-老板说明书/老板说明书.md" if settings.kb_root else Path("/nonexistent")
    return path.read_text(encoding="utf-8")[:6000] if path.exists() else ""


def exemplar_paths(settings: Settings, query: str, mode: str) -> list[Path]:
    script = (skill_dir(settings) or Path("/nonexistent")) / "scripts/ip_content_context.py"
    if not settings.kb_root or not script.exists():
        return []
    proc = subprocess.run([sys.executable, str(script), "--mode", mode, "--query", query[:200], "--limit", "2",
                           "--kb-root", settings.kb_root], capture_output=True, text=True, timeout=60)
    found = re.findall(r"^- `(7-历史表达与飞书精选/公众号原文/[^`]+)`", proc.stdout, re.M)
    return [Path(settings.kb_root) / rel for rel in found]


def _article(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("\n---\n📌")[0]


@lru_cache(maxsize=1)
def _voice_check_module(path: str):
    spec = importlib.util.spec_from_file_location("voice_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def voice_flags(settings: Settings, text: str, mode: str) -> list[str]:
    """Run the skill's voice check in-process; returns human-readable flags."""
    script = (skill_dir(settings) or Path("/nonexistent")) / "scripts/voice_check.py"
    if not script.exists() or not (script.parent.parent / "references/voice_baseline.json").exists():
        return []
    vc = _voice_check_module(str(script))
    base = __import__("json").loads((skill_dir(settings) / "references/voice_baseline.json").read_text(encoding="utf-8"))
    m = vc.measure(text)
    long_form = m["chars"] >= 600 and mode == "公众号"
    flags = []
    for name, (_, direction) in vc.FEATURES.items():
        b = base[name]
        if direction == "over" and m[name] > max(b["p90"] * 1.5, b["p90"] + 0.5):
            flags.append(f"{name} 偏多")
        if direction == "under" and long_form and m[name] < b["p10"]:
            flags.append(f"{name} 偏少")
    hits = sorted(set(re.findall(vc.HARD_TELLS, vc.clean(text))))
    if hits:
        flags.append(f"出现 {persona(settings)['name']} 基本不用的词：" + "、".join(hits))
    return flags


EXTRACT = """你在帮 {name}{identity_paren}从他的工作记录里找能发 X 和公众号的选题。

下面是一份原始素材，可能是他的录音转写、随手记、会议纪要或飞书文档。请找出 0 到 6 个真正值得写的选题。值得写的标准：有他自己的观察、判断或经历，有具体的事、数字或案例，别人看了能学到东西或者想讨论。流水账、纯事务安排、只有别人观点的部分不要。宁缺毋滥，没有就返回空列表。

取材规则：
- quotes 里只放 {name} 本人说过或写过的原话，逐字摘录，每条不超过 80 字。会议里别人说的话不要放进 quotes，也不要在任何字段里写出别人的名字和原话。
{table_rule}
- 会议逐字稿里发言人常常只标成 Speaker 1、Speaker 2。只有上下文能明确看出是 {name}（{speaker_hint}）时，才把那段当作他的原话；看不出来的，只能当背景，不能放进 quotes。
- 不要编造素材里没有的事实和数字。
- 涉及私人生活、健康、家庭、钱的具体数目、未公开的合作方，只在 notes 里提醒，不写进选题。

他已经在公众号发过下面这些文章。和其中某篇主题基本重复的选题不要列；如果是已发文章里没展开的新角度、新案例、新数据，可以列，在 notes 里写明和哪篇有关。
{published}

工作台里已经有下面这些选题（包括他挑过和不要了的）。和其中某个基本重复的不要再列，同一场直播或会议的文字稿、重写稿常常会分好几份进来，同一个观点只算一次；同一件事有了新的案例、新的数据或者新的判断，可以列，在 notes 里写明和哪个已有选题有关。
{existing}

{name} 的身份和边界（节选自《{manual_title}》）：
{manual}

只输出一个 JSON 对象，不要别的文字：
{{"ideas": [{{"title": "一句话选题，像他会起的标题", "angle": "切入角度，两三句话，用他的口吻", "platforms": ["公众号" 或 "X"，可多选], "quotes": ["他的原话"], "notes": "需要他补充或确认的地方"}}]}}

素材（来源：{source}，标题：{title}）：
<<<
{body}
>>>
"""

DRAFT_WECHAT = """你在帮 {name} 写一篇公众号底稿。这是给他自己改的底稿，不是替他定稿：观点和事实必须是他的，留给他改的地方要在 notes 里点明。

先读他的文风指南，再读他的原文样本，然后写。

<文风指南>
{guide}
</文风指南>

<他的原文样本一（完整读）>
{exemplar1}
</他的原文样本一>

<他的原文样本二（只看开头结尾的写法）>
{exemplar2}
</他的原文样本二>

<身份和边界>
{manual}
</身份和边界>

这次要写的选题：{idea_title}
切入角度：{idea_angle}
他在素材里的原话（可以直接用，也可以改写成更顺的说法）：
{quotes}

原始素材（只能用这里有的事实，没有的不要编）：
<<<
{material}
>>>

要求：2000 到 3500 字，Markdown，不要冒号小标题、不要格言式结尾。口头语每篇最多用一两个，不要照搬样本里的类比。素材少的地方就写短，不要灌水。

排版（公众号和 X 长文都会按这个转换，{name} 之后会在编辑器里自己调）：
- 小标题用 `## 一、……`，一节里还要再分层就用 `### `，再下一层用 `#### `，一般用不到三层。
- 每节挑一两句最关键的话加粗（`**…**`），加粗的是句子里的判断或结论，不要加粗段首、不要整段加粗。
- 全文最核心的一两个判断用高亮（`==…==`），全文不超过三处。
- 并列的几个要点用列表；引用别人或他自己说过的原话用 `> `。
- 不要表情符号、不要分割线堆砌、不要花哨格式。

{cases_rule}

检索原文样本和文风检查都由工作台自动做了，notes 里不要提脚本、工具或检查流程，只写跟这篇稿子有关的事。

需要 {name} 亲自写一句、或者要他确认的地方，不要在正文里用【】占位，放进 comments：quote 是正文里你想挂批注的那一小段原文（纯文字，不带 Markdown 符号，40 字以内），text 是要他做的事。

只输出一个 JSON 对象：
{{"title": "标题", "body": "正文 Markdown（不含标题）", "notes": "改稿备注：要核实的事实、用到的素材和出处", "comments": [{{"quote": "正文原文里的一小段", "text": "要他补的话或要确认的事"}}]}}
"""

DRAFT_X = """你在帮 {name} 写 X（推特）上的中文内容底稿，给他自己改。

<文风指南>
{guide}
</文风指南>

{x_style}

{cases_rule}

<他的原文样本（看语气）>
{exemplar1}
</他的原文样本>

选题：{idea_title}
切入角度：{idea_angle}
他的原话：
{quotes}

原始素材（只能用这里有的事实）：
<<<
{material}
>>>

格式要求：每条推文之间用单独一行 --- 隔开。每条按 X 的计数不超过 280（中文一个字算 2，所以每条最多约 130 个汉字）。内容够就写成 3 到 8 条的长串，不够就写一条。带链接的推文更贵，除非素材里有必须放的链接，否则不放链接。

检索原文样本和文风检查都由工作台自动做了，notes 里不要提脚本、工具或检查流程，只写跟这篇稿子有关的事。

只输出一个 JSON 对象：
{{"title": "给自己看的标题", "body": "推文正文，用 --- 分隔", "notes": "改稿备注"}}
"""

REVISE = """下面这篇底稿被检查出一些 AI 腔的信号：{flags}

请对照文风指南改写有问题的句子，意思和事实不变，不要为了过检查硬塞“吧”“呢”。只输出同样结构的 JSON 对象（title、body、notes）。

<文风指南摘要>
{guide}
</文风指南摘要>

<底稿>
{draft_json}
</底稿>
"""


CASES_RULE = """需要案例佐证、而素材里又没有的时候，可以用生财 MCP 的只读工具去搜（深海圈帖子、精华区、项目案例库、航海资料），最多搜 4 次，素材够就不用搜。只用真实搜到的内容，不编数字和故事。用到的每个案例在 notes 里写清楚：标题、作者、链接、用在了哪一段；圈友的案例要提醒 {name} 发之前确认能不能公开引用、要不要打码。不要写进圈友的联系方式、收入明细等个人信息。"""

CASES = """{name} 在写一篇稿子，想找能用的案例。请用生财 MCP 的只读工具搜一下（深海圈帖子、精华区、项目案例库、航海资料都可以），挑最贴切的 3 到 6 个。只用真实搜到的内容，不编。

他要找的：{query}

稿子标题：{title}
稿子开头（帮你判断语境）：
<<<
{excerpt}
>>>

只输出一个 JSON 对象：
{{"cases": [{{"title": "原帖标题", "author": "作者昵称", "url": "原帖链接", "summary": "两三句话讲清这个案例具体发生了什么、有什么数字", "fit": "适合用在稿子的哪个地方、怎么用", "public": "这是公开内容还是圈内内容"}}]}}
"""


def build_cases(query: str, title: str, body: str, settings: Settings | None = None) -> str:
    who = persona(settings or Settings.load())
    return CASES.format(name=who["name"], query=query[:300], title=title or "（未定）", excerpt=body[:2500] or "（还没写）")


def case_search_on(settings: Settings) -> bool:
    return settings.case_search == "scys"


def cases_rule(settings: Settings) -> str:
    return CASES_RULE.replace("{name}", persona(settings)["name"]) if case_search_on(settings) else ""


REVISE_COMMENTS = """你在帮 {name} 按他的批注修改一篇稿子。稿子用 Markdown 写，他会在编辑器里看到排版后的样子。

<文风指南摘要>
{guide}
</文风指南摘要>

{lessons}

<稿子标题>
{title}
</稿子标题>

<稿子正文>
{body}
</稿子正文>

<这次要处理的批注（最后一句是 {name} 说的）>
{todo}
</这次要处理的批注>

<其他还开着的批注（只作背景，不用回复）>
{others}
</其他还开着的批注>

怎么改：
- 只改批注涉及的地方；批注要求调整全文的，才动全文。没提到的段落一个字都不要改。
- {name} 在回复里亲自写的句子，原样放进正文，不要润色他的话。
- 正文里 `![](/api/images/数字)` 这样的图片行必须原样保留（批注要求移动就移动位置，要求删才删）。
- 如果正文是 X 推文串（推文之间用单独一行 `---` 分隔），保持这种分隔，每条按 X 的计数不超过 280（中文一个字算 2）。
- 排版照旧：小标题 `## `、`### `、`#### `，关键句 `**加粗**`，最核心的判断 `==高亮==`（全文不超过三处）。加粗或高亮以中文标点结尾、后面紧跟文字时，把标点放到符号外面。
- 不编事实、数字、案例和他没说过的观点。需要他补充或确认的，不要写进正文（不要用【】占位），而是放进 new_comments，quote 写正文里你想挂批注的那一小段原文（纯文字，不带 Markdown 符号，40 字以内）。
{case_line}
- 每条批注都要回复：做了什么改动，或者为什么没改、需要他定什么。完全处理好的 resolved 填 true，还需要他确认的填 false。回复用平常说话的口吻，一两句话。

只输出一个 JSON 对象：
{{"title": "标题（没让改就原样）", "body": "改好的完整正文 Markdown", "replies": [{{"thread_id": 批注编号, "reply": "回复", "resolved": true}}], "new_comments": [{{"quote": "正文原文里的一小段", "text": "要他确认或补充的事"}}], "summary": "一句话说这一轮改了什么"}}
"""


def style_lessons_text() -> str:
    from .learning import lessons_prompt
    return lessons_prompt()


def build_revise_comments(settings: Settings, draft: dict, todo: str, others: str) -> str:
    case_line = "- 需要案例的，可以用生财 MCP 的只读工具去搜，出处写在回复里。" if case_search_on(settings) else ""
    return REVISE_COMMENTS.format(guide=voice_guide(settings)[:6000], lessons=style_lessons_text(), title=draft["title"],
                                  body=draft["body"], todo=todo, others=others or "（无）", case_line=case_line,
                                  **persona(settings))


def build_extract(settings: Settings, material: dict, existing: list[str] | None = None) -> str:
    from .sources import published_titles
    published = "\n".join(f"- {t}" for t in published_titles(settings.kb_root)) or "（暂无）"
    existing_text = "\n".join(f"- {t}" for t in existing or [] if t.strip()) or "（暂无）"
    return EXTRACT.format(manual=boss_manual(settings)[:3000], published=published, existing=existing_text,
                          source=material["source"], title=material["title"], body=material["body"][:40000],
                          **persona(settings))


def build_draft(settings: Settings, platform: str, idea: dict, material: dict | None, quotes: list[str]) -> str:
    mode = "X" if platform == "x" else "公众号"
    query = f"{idea['title']} {idea.get('angle') or ''}"
    paths = exemplar_paths(settings, query, mode)
    ex1 = _article(paths[0])[:9000] if paths else ""
    ex2 = ""
    if len(paths) > 1:
        t = _article(paths[1])
        ex2 = t[:1200] + "\n……\n" + t[-800:]
    fields = dict(guide=voice_guide(settings), exemplar1=ex1, exemplar2=ex2, manual=boss_manual(settings)[:3000],
                  idea_title=idea["title"], idea_angle=idea.get("angle") or "",
                  quotes="\n".join(f"- {q}" for q in quotes) or "（无）",
                  material=(material or {}).get("body", "")[:30000],
                  cases_rule=cases_rule(settings) + "\n\n" + style_lessons_text(), **persona(settings))
    return (DRAFT_X if platform == "x" else DRAFT_WECHAT).format(**fields)


def build_revise(settings: Settings, flags: list[str], draft_json: str) -> str:
    return REVISE.format(flags="；".join(flags), guide=voice_guide(settings)[:6000], draft_json=draft_json)
