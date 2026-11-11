"""Local Whisper (mlx-whisper), run in a child process that exits when its work is done.

Inside the long-lived server Whisper never gave its memory back. mlx-whisper keeps
the 1.5 GB model loaded for good, and MLX keeps every GPU buffer it frees in a cache
for reuse whose default limit is the whole memory limit (15 GB on a 16 GB Mac).
Each transcribed line added about 25 MB: 123 real voice lines took one process
to 4.5 GB, and after 4.5 days of daily videos the server held 3.8 GB resident
plus 2 GB compressed, which went to swap and filled the disk.

A child process returns all of it when it exits. One child serves a whole batch
(every new line of a render), so the model loads once per batch, in about 2 s.
The server itself never imports mlx-whisper, MLX, numba or scipy.

The child is a plain ``python -c`` with this interpreter and this sys.path, not
multiprocessing: spawn re-imports the caller's main script, which breaks any
script that calls this without an ``if __name__ == "__main__"`` guard."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

_LOCK = threading.Lock()  # one child at a time: each holds the 1.5 GB model
CHILD = "from content_workbench.whisper import _child; _child()"


class WhisperError(RuntimeError):
    pass


def available() -> bool:
    """Installed? Answered without importing it, so the server never loads MLX."""
    return importlib.util.find_spec("mlx_whisper") is not None


def transcribe(paths: list[Path], model: str, prompt: str = "", words: bool = False,
               each: Callable[[int], None] | None = None) -> list[dict]:
    """Every file in one child process, in order: {"text", "words": [{"word", "start", "end"}]}.
    ``each(n)`` is called as the n-th file (1-based) is done."""
    if not paths:
        return []
    job = json.dumps({"paths": [str(p) for p in paths], "model": model, "prompt": prompt, "words": words})
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    out = []
    with _LOCK, subprocess.Popen([sys.executable, "-c", CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=env) as proc:
        err: list[str] = []
        drain = threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)  # a full stderr pipe would stall the child
        drain.start()
        try:
            proc.stdin.write(job)
            proc.stdin.close()
            for line in proc.stdout:
                out.append(json.loads(line))
                if each:
                    each(len(out))
            proc.wait()
        except BaseException:
            proc.kill()
            raise
        drain.join(5)
    if proc.returncode or len(out) != len(paths):
        raise WhisperError(f"语音识别出错（{proc.returncode}）：{''.join(err)[-400:].strip()}")
    return out


def _child() -> None:
    """In the child: read the job from stdin, write one JSON line per file. Library
    output goes to stderr, so stdout carries nothing but results."""
    results = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    job = json.load(sys.stdin)
    import mlx.core as mx
    import mlx_whisper
    for path in job["paths"]:
        res = mlx_whisper.transcribe(path, path_or_hf_repo=job["model"], language="zh", word_timestamps=job["words"],
                                     initial_prompt=job["prompt"])
        mx.clear_cache()  # keeps a long batch from piling up cached buffers before the child exits
        words = [{"word": w["word"], "start": w["start"], "end": w["end"]}
                 for seg in res.get("segments", []) for w in seg.get("words", [])] if job["words"] else []
        results.write(json.dumps({"text": res.get("text") or "", "words": words}, ensure_ascii=False) + "\n")
        results.flush()
