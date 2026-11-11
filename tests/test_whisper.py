"""Whisper runs in a child process, so the server never holds its memory (whisper.py)."""

import math
import os
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from content_workbench import video, whisper

from test_app import client, login


def tone(path: Path, seconds: float = 1.0, rate: int = 24000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(seconds * rate))))
    return path


def test_status_and_availability_never_load_mlx_into_the_server():
    """/api/status asks whether transcription works every few seconds; asking must not import MLX."""
    code = ("import sys\n"
            "from test_app import client, login\n"
            "c = client(); login(c)\n"
            "assert c.get('/api/status').json()['transcription'] in (True, False)\n"
            "from content_workbench import sources, video\n"
            "sources.transcription_available(), video.whisper_available()\n"
            "loaded = sorted(m for m in ('mlx', 'mlx_whisper', 'numba', 'scipy', 'torch') if m in sys.modules)\n"
            "assert not loaded, loaded\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(Path(__file__).parent), *sys.path])}
    assert env.get("CW_DATA_DIR")  # the test's private data dir (conftest), never the live one
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          cwd=Path(__file__).parent, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]


def test_new_lines_of_a_render_are_heard_in_one_whisper_run(monkeypatch, tmp_path):
    calls, progress = [], []

    def fake(paths, model, prompt="", words=False, each=None):
        calls.append([Path(p).name for p in paths])
        assert model == video.WHISPER and words and prompt == video.vocab_prompt("飞书")
        out = []
        for n, _ in enumerate(paths, 1):
            out.append({"text": "一二三", "words": [{"word": "一二", "start": 0.3, "end": 0.7}, {"word": "三。", "start": 0.7, "end": 0.9}]})
            each and each(n)
        return out
    monkeypatch.setattr(whisper, "transcribe", fake)
    todo = [("一二三", tone(tmp_path / f"raw-{n}.wav"), tmp_path / f"voice-{n}.wav") for n in range(3)]
    lines = video.timed_lines(todo, "飞书", True, each=lambda n, of: progress.append((n, of)))
    assert calls == [["voice-0.wav", "voice-1.wav", "voice-2.wav"]] and progress == [(1, 3), (2, 3), (3, 3)]
    assert [ln["times"][0] for ln in lines] == [(0.3, 0.5)] * 3 and all(ln["duration"] >= 2.0 for ln in lines)
    # times are kept beside each voice: the same lines again need no Whisper at all
    assert [ln["times"] for ln in video.timed_lines(todo, "飞书", True)] == [ln["times"] for ln in lines] and len(calls) == 1
    # one new line: Whisper hears just that one
    video.timed_lines(todo + [("四五", tone(tmp_path / "raw-9.wav"), tmp_path / "voice-9.wav")], "飞书", True)
    assert calls[-1] == ["voice-9.wav"]
    assert video.timed_line("一二三", todo[0][1], tmp_path / "again.wav", "飞书", True)["times"] == lines[0]["times"]


SMALL = "mlx-community/whisper-small-mlx"


def _cached(repo: str) -> bool:
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME") or Path.home() / ".cache/huggingface") / "hub")
    return (hub / f"models--{repo.replace('/', '--')}" / "snapshots").is_dir()


@pytest.mark.skipif(not whisper.available() or not _cached(SMALL), reason="needs mlx-whisper and a cached small model")
def test_real_whisper_runs_in_a_child_that_exits(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    files = [tone(tmp_path / f"t{n}.wav", 1.0 + n) for n in range(2)]
    done = []
    out = whisper.transcribe(files, SMALL, "以下是普通话口播。", words=True, each=done.append)
    assert done == [1, 2] and len(out) == 2 and all(set(r) == {"text", "words"} for r in out)
    assert "mlx_whisper" not in sys.modules and "mlx" not in sys.modules
    with pytest.raises(whisper.WhisperError, match="语音识别出错"):  # a file it can't read fails the batch, with the reason
        whisper.transcribe([tmp_path / "missing.wav"], SMALL)
