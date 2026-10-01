"""Tests für sandbox/qwen_webhook.py (Worker-Log per HA-Webhook), ohne Netzwerk."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "qwen_webhook.py"
_spec = importlib.util.spec_from_file_location("qwen_webhook", _PATH)
q = importlib.util.module_from_spec(_spec)
sys.modules["qwen_webhook"] = q
_spec.loader.exec_module(q)


def _append(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def test_follower_starts_at_end_like_tail_n0(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text("alt 1\nalt 2\n", encoding="utf-8")
    f = q.Follower(str(log))
    assert f.poll() == []                       # Vorhandenes nicht erneut senden
    _append(log, "neu 1\nneu 2\n")
    assert f.poll() == ["neu 1", "neu 2"]
    assert f.poll() == []


def test_follower_waits_for_complete_line(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text("", encoding="utf-8")
    f = q.Follower(str(log))
    _append(log, "halbe Zei")
    assert f.poll() == []
    _append(log, "le\n")
    assert f.poll() == ["halbe Zeile"]


def test_follower_handles_truncate(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text("x" * 100 + "\n", encoding="utf-8")
    f = q.Follower(str(log))
    log.write_text("nach Kürzen\n", encoding="utf-8")   # kürzer als vorher gelesene Position
    assert f.poll() == ["nach Kürzen"]


@pytest.mark.skipif(os.name == "nt", reason="Windows kann eine geöffnete Datei nicht umbenennen; Ziel ist Linux")
def test_follower_handles_replaced_file(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text("alt\n", encoding="utf-8")
    f = q.Follower(str(log))
    os.replace(log, tmp_path / "worker.log.1")
    log.write_text("neue Datei\n", encoding="utf-8")
    assert f.poll() == ["neue Datei"]


def test_follower_missing_file(tmp_path):
    f = q.Follower(str(tmp_path / "fehlt.log"))
    assert f.poll() == []
    (tmp_path / "fehlt.log").write_text("da\n", encoding="utf-8")
    assert f.poll() == ["da"]


def test_payload_json_and_limit():
    data = json.loads(q.payload(["a", "b"]))
    assert data == {"lines": "a\nb"}
    big = json.loads(q.payload(["x" * 5000]))
    assert len(big["lines"]) == q.MAX_CHARS


class _Resp:
    def __init__(self, status): self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_send_posts_json():
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), body=req.data,
                    ctype=req.get_header("Content-type"))
        return _Resp(200)
    assert q.send("http://ha.test:8123/api/webhook/abc", ["zeile"], opener=opener)
    assert seen["method"] == "POST" and seen["ctype"] == "application/json"
    assert json.loads(seen["body"]) == {"lines": "zeile"}


def test_send_failure_returns_false():
    def opener(req, timeout):
        raise OSError("Netz weg")
    assert q.send("http://ha.test", ["x"], opener=opener) is False


class _FakeFollower:
    def __init__(self, batches): self.batches = list(batches)
    def poll(self): return self.batches.pop(0) if self.batches else []


def test_step_keeps_lines_on_failure_and_sends_later():
    f = _FakeFollower([["a"], ["b"], []])
    sent = []
    pending = q.step(f, [], "u", sender=lambda u, lines: False)
    assert pending == ["a"]
    pending = q.step(f, pending, "u", sender=lambda u, lines: sent.append(lines) or True)
    assert sent == [["a", "b"]] and pending == []


def test_step_caps_pending():
    f = _FakeFollower([["y" * 900] * 5])
    pending = q.step(f, [], "u", sender=lambda u, lines: False)
    assert sum(len(x) + 1 for x in pending) <= q.MAX_CHARS
    assert pending and pending[-1] == "y" * 900   # die neuesten Zeilen bleiben
