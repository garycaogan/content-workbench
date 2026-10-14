"""Material sources: Feishu Minutes and docs through the owner's lark-cli login,
plus local audio transcription with mlx-whisper (optional dependency)."""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import data_dir

LARK_ENV = {**os.environ, "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1", "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"}


class SourceError(RuntimeError):
    pass


def _lark(args: list[str], cwd: Path | None = None, timeout: int = 120) -> dict:
    binary = shutil.which("lark-cli") or str(Path.home() / ".local/bin/lark-cli")
    proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout, env=LARK_ENV, cwd=cwd)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        raise SourceError(f"lark-cli 没有返回 JSON：{(proc.stderr or proc.stdout)[:300]}") from None
    if out.get("ok") is False:
        raise SourceError((out.get("error") or {}).get("message", "lark-cli 调用失败")[:300])
    return out.get("data") or out


def list_minutes(start: str, end: str) -> list[dict]:
    """Minutes the author owns or took part in, newest first, deduplicated by token."""
    seen: dict[str, dict] = {}
    for flag in ("--owner-ids", "--participant-ids"):
        token = None
        for _ in range(10):
            args = ["minutes", "+search", "--as", "user", flag, "me", "--start", start, "--end", end,
                    "--page-size", "30", "--format", "json"]
            if token:
                args += ["--page-token", token]
            data = _lark(args)
            for item in data.get("items") or []:
                info = (item.get("display_info") or "").split("\n")
                seen[item["token"]] = {"token": item["token"], "title": info[0] if info else item["token"],
                                       "description": (item.get("meta_data") or {}).get("description", ""),
                                       "link": (item.get("meta_data") or {}).get("app_link", "")}
            token = data.get("page_token")
            if not data.get("has_more") or not token:
                break
    return list(seen.values())


def fetch_minute_transcript(token: str) -> str:
    work = data_dir() / "tmp"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        _lark(["vc", "+notes", "--as", "user", "--minute-tokens", token, "--output-dir", "./out"], cwd=Path(tmp), timeout=300)
        files = list(Path(tmp, "out").rglob("transcript.txt"))
        if not files:
            raise SourceError("这条妙记没有逐字稿")
        return files[0].read_text(encoding="utf-8")


def fetch_doc(url: str) -> tuple[str, str]:
    data = _lark(["docs", "+fetch", "--as", "user", "--doc", url, "--doc-format", "markdown"])
    doc = data.get("document") or {}
    content = doc.get("content") or data.get("markdown") or ""
    title = doc.get("title") or ""
    if not title and content.startswith("<title>"):
        title = content[7:content.find("</title>")]
    if not title:
        first = content.strip().splitlines()[0] if content.strip() else url
        title = first.lstrip("# ")[:80]
    return title, content


def transcription_available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


WHISPER_MODEL = os.environ.get("CW_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")


def transcribe(path: Path) -> str:
    """Local transcription on Apple silicon; the audio never leaves the machine."""
    try:
        import mlx_whisper
    except Exception:
        raise SourceError("本机还没装语音转写组件（mlx-whisper）") from None
    from .config import Settings
    vocab = Settings.load().transcribe_vocab.strip()
    result = mlx_whisper.transcribe(str(path), path_or_hf_repo=WHISPER_MODEL, language="zh",
                                    initial_prompt="以下是普通话的口述工作记录。" + (f"常见词：{vocab}。" if vocab else ""))
    return (result.get("text") or "").strip()


# ------------------------------------------------------------------ Feishu library

LIBRARY_TYPES = "docx,doc,sheet,bitable,wiki,mindnote"

MEETING = re.compile(r"文字记录|智能纪要|会议纪要|的视频会议|我的笔记：|^新录音")
ADMIN = re.compile(r"报名|名单|面试|招聘|\bJD\b|账号密码|记账|台账|续费|表单|问卷|Client Area|控制台|定价|待办|分组表|登记表|结算|账号登记|账号管理|申诉材料|合同|发票")
AGENT = re.compile(r"｜\s*20\d\d[-.]\d\d|审计|日报|证据|方案（第|第[二三四]版|初稿|底稿|Codex|Claude|案例库｜|索引｜|问题汇总|需求清单|提示词模板汇总|测试报告|复盘汇总|待办（20")


def _month_windows(start: datetime.date) -> list[tuple[str, str]]:
    out = []
    d0 = start
    while d0 <= datetime.date.today():
        d1 = (d0.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        out.append((d0.isoformat(), d1.isoformat()))
        d0 = d1
    return out


def list_my_docs(start: datetime.date = datetime.date(2023, 1, 1)) -> list[dict]:
    """Every doc, sheet and base the author originally created. Search caps a query at
    400 hits, so it walks month by month."""
    seen: dict[str, dict] = {}
    for since, until in _month_windows(start):
        token = None
        for _ in range(30):
            args = ["drive", "+search", "--as", "user", "--created-by-me", "--created-since", since, "--created-until", until,
                    "--doc-types", LIBRARY_TYPES, "--page-size", "20", "--sort", "create_time"]
            if token:
                args += ["--page-token", token]
            data = _lark(args)
            for r in data.get("results") or []:
                m = r.get("result_meta") or {}
                key = m.get("token")
                if not key or key in seen:
                    continue
                title = re.sub(r"</?em>", "", r.get("title_highlighted") or "").replace("&amp;", "&").strip()
                seen[key] = {"token": key, "title": title or "Untitled", "type": (m.get("doc_types") or "").lower(),
                             "url": m.get("url"), "created": m.get("create_time"), "updated": m.get("update_time")}
            token = data.get("page_token")
            if not data.get("has_more") or not token:
                break
    return sorted(seen.values(), key=lambda x: -(x["created"] or 0))


def classify_doc(title: str) -> str:
    if MEETING.search(title):
        return "meeting"
    if ADMIN.search(title):
        return "admin"
    if AGENT.search(title):
        return "generated"
    if re.fullmatch(r"(Untitled.*|无标题.*|\d+|zero|1|问题)", title.strip()):
        return "untitled"
    return "unsure"


def _norm(t: str) -> str:
    return re.sub(r"[\s【】\[\]（）()｜|：:，,。！!？?“”\"'、&amp;-]+", "", t).lower()


def published_titles(kb_root: str) -> list[str]:
    if not kb_root:
        return []
    index = Path(kb_root) / "7-历史表达与飞书精选/公众号原文索引.md"
    if not index.exists():
        return []
    return re.findall(r"\|\s*\d{8}\s*\|\s*\[\[[^|\]]+\\?\|([^\]]+)\]\]", index.read_text(encoding="utf-8"))


def looks_published(title: str, published: list[str]) -> bool:
    n = _norm(title)
    if len(n) < 6:
        return False
    for p in published:
        q = _norm(p)
        if len(q) >= 6 and (q in n or n in q or q[:12] == n[:12]):
            return True
    return False


TABLE_CHAR_CAP = 60000


def _resolve(url: str, kind: str) -> str:
    """Return the real sheet/base token behind a wiki URL (or the token in a direct URL)."""
    if "/wiki/" in url:
        node = _lark(["wiki", "+node-get", "--as", "user", "--node-token", url])
        node = node.get("node") or node
        return node.get("obj_token") or node.get("token")
    return url.rstrip("/").split("/")[-1].split("?")[0]


def fetch_table(url: str, kind: str) -> str:
    """Flatten a spreadsheet or Base into readable text: each sub-sheet/table as
    CSV-ish rows or a markdown table, capped so one huge table can't flood a prompt."""
    parts: list[str] = []
    if kind == "sheet":
        info = _lark(["sheets", "+workbook-info", "--as", "user", "--url", url])
        for sh in (info.get("sheets") or [])[:6]:
            if sh.get("is_hidden") or sh.get("resource_type") not in (None, "sheet"):
                continue
            rows = min(int(sh.get("row_count") or 200), 400)
            data = _lark(["sheets", "+csv-get", "--as", "user", "--url", url, "--sheet-id", sh["sheet_id"],
                          "--range", f"A1:Z{rows}", "--include-row-prefix=false", "--max-chars", "40000"])
            csv = data.get("annotated_csv") or data.get("csv") or ""
            if csv.strip(","+"\n "):
                parts.append(f"## {sh.get('sheet_name')}\n{csv}")
    else:
        base = _resolve(url, "bitable")
        tables = _lark(["base", "+table-list", "--as", "user", "--base-token", base])
        for tb in (tables.get("tables") or tables.get("items") or [])[:4]:
            tid = tb.get("table_id") or tb.get("id")
            proc = subprocess.run([shutil.which("lark-cli") or "lark-cli", "base", "+record-list", "--as", "user", "--base-token", base,
                                   "--table-id", tid, "--limit", "200", "--format", "markdown"],
                                  capture_output=True, text=True, timeout=180, env=LARK_ENV)
            text = proc.stdout.replace("`_record_id` is metadata for record operations, not a table field.", "").strip()
            if text and not text.startswith("{"):
                parts.append(f"## {tb.get('name') or tb.get('table_name')}\n{text}")
        if not parts:
            raise SourceError("没读到表格内容")
    out = "\n\n".join(parts)
    return out[:TABLE_CHAR_CAP]
