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


def test_payload_strips_ansi():
    data = json.loads(q.payload(["\x1b[32mErfolg\x1b[0m", "\x1b[1;90mDetails\x1b[0m"]))
    assert data == {"lines": "Erfolg\nDetails"}


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


# --- Slot-Telemetrie für Home Assistant (#14) ---

def test_read_slots_missing_dir_is_empty(tmp_path):
    assert q.read_slots(str(tmp_path / "fehlt")) == {}


def test_read_slots_skips_invalid_files(tmp_path):
    (tmp_path / "slot-1.json").write_text("{kaputt", encoding="utf-8")
    (tmp_path / "slot-2.json").write_text(json.dumps(["kein dict"]), encoding="utf-8")
    (tmp_path / "anderes.json").write_text(json.dumps({"slot": "9"}), encoding="utf-8")
    assert q.read_slots(str(tmp_path)) == {}


def test_read_slots_marks_orphan_running_as_idle(tmp_path):
    (tmp_path / "slot-1.json").write_text(
        json.dumps({"slot": "1", "status": "running", "pid": 99999, "issue": 14}), encoding="utf-8")
    slots = q.read_slots(str(tmp_path), is_pid_alive=lambda pid: False)
    assert slots["1"]["status"] == "idle"
    assert slots["1"]["message"] == "Prozess beendet"
    assert slots["1"]["issue"] == 14          # der Rest der Meldung bleibt erhalten


def test_read_slots_keeps_idle_and_live_running(tmp_path):
    (tmp_path / "slot-1.json").write_text(json.dumps({"slot": "1", "status": "idle"}), encoding="utf-8")
    (tmp_path / "slot-2.json").write_text(
        json.dumps({"slot": "2", "status": "running", "pid": 4}), encoding="utf-8")
    slots = q.read_slots(str(tmp_path), is_pid_alive=lambda pid: True)
    assert slots["1"]["status"] == "idle" and slots["2"]["status"] == "running"
    assert "message" not in slots["2"]


def test_pid_is_active_without_pid():
    assert q.pid_is_active(None) is False
    assert q.pid_is_active(0) is False
    assert q.pid_is_active(-1) is False
    assert q.pid_is_active(os.getpid()) is True


def test_pid_is_active_linux_nutzt_weiter_os_kill_mit_signal_null(monkeypatch):
    """Der Linux-Zweig bleibt unverändert: os.kill(pid, 0) entscheidet (#118)."""
    aufrufe: list[tuple[int, int]] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(q.os, "kill", lambda pid, sig: aufrufe.append((pid, sig)))
    assert q.pid_is_active(4242) is True
    assert aufrufe == [(4242, 0)]

    def kein_prozess(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(q.os, "kill", kein_prozess)
    assert q.pid_is_active(4242) is False


def test_payload_without_slots_omits_key():
    assert json.loads(q.payload(["a"])) == {"lines": "a"}
    assert json.loads(q.payload(["a"], slots={})) == {"lines": "a", "slots": {}}


def test_send_includes_slots_attribute():
    seen = {}

    def opener(req, timeout):
        seen.update(body=req.data)
        return _Resp(200)
    assert q.send("http://ha.test/api/webhook/abc", ["zeile"],
                  slots={"1": {"status": "idle"}}, opener=opener)
    assert json.loads(seen["body"]) == {"lines": "zeile", "slots": {"1": {"status": "idle"}}}


def test_step_without_slots_is_backward_compatible():
    # Ohne last_slots bleibt es beim alten Verhalten: Rückgabe ist die Liste der offenen Zeilen.
    f = _FakeFollower([["a"]])
    assert q.step(f, [], "u", sender=lambda url, lines: True) == []


def test_step_sends_slots_only_on_change(tmp_path):
    (tmp_path / "slot-1.json").write_text(json.dumps({"slot": "1", "status": "idle"}), encoding="utf-8")
    sent = []

    def sender(url, lines, slots=None):
        sent.append(slots)
        return True

    f = _FakeFollower([[], []])
    pending, last = q.step(f, [], "u", last_slots={}, slot_dir=str(tmp_path), sender=sender)
    assert len(sent) == 1 and last["1"]["status"] == "idle"
    pending, last = q.step(f, pending, "u", last_slots=last, slot_dir=str(tmp_path), sender=sender)
    assert len(sent) == 1                      # unverändert -> kein zweites Senden
    (tmp_path / "slot-1.json").write_text(
        json.dumps({"slot": "1", "status": "running", "pid": 4}), encoding="utf-8")
    pending, last = q.step(f, pending, "u", last_slots=last, slot_dir=str(tmp_path),
                           sender=sender, is_pid_alive=lambda pid: True)
    assert len(sent) == 2 and last["1"]["status"] == "running"


def test_step_keeps_lines_when_slots_send_fails(tmp_path):
    (tmp_path / "slot-1.json").write_text(json.dumps({"slot": "1", "status": "idle"}), encoding="utf-8")
    f = _FakeFollower([["a"], []])
    pending, last = q.step(f, [], "u", last_slots={}, slot_dir=str(tmp_path),
                           sender=lambda url, lines, slots=None: False)
    assert pending == ["a"] and last == {"1": {"slot": "1", "status": "idle"}}


# --- Webhook-ID oder URL; das Geheimnis erscheint nie in Ausgaben ---

SECRET = "3a" * 24   # Platzhalter in der Form einer echten ID (48 Hex-Zeichen)


def test_webhook_url_accepts_bare_id():
    assert q.webhook_url(SECRET, "http://ha.test:8123/") == f"http://ha.test:8123/api/webhook/{SECRET}"


def test_webhook_url_accepts_full_url():
    url = f"http://192.168.0.148:8123/api/webhook/{SECRET}"
    assert q.webhook_url(f"  {url}\n") == url


@pytest.mark.parametrize("value", ["", "kurz", "ftp://x/api/webhook/abc", "http://ha.test:8123/", "id mit leerzeichen 1234567890"])
def test_webhook_url_rejects_invalid(value):
    assert q.webhook_url(value) is None


def test_main_invalid_value_does_not_print_it(monkeypatch, capsys):
    monkeypatch.setenv("HA_WEBHOOK_URL", "geheim " + SECRET)   # ungültig (Leerzeichen)
    with pytest.raises(SystemExit) as exc:
        q.main()
    out = capsys.readouterr()
    assert SECRET not in str(exc.value) + out.out + out.err


def test_send_error_does_not_print_url(capsys):
    def opener(req, timeout):
        raise ValueError(f"unknown url type: {req.full_url}")   # so sah der Fehler am 01.10. aus
    assert q.send(f"http://ha.test/api/webhook/{SECRET}", ["x"], opener=opener) is False
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    assert "ValueError" in out.err


def test_send_bad_url_is_caught_without_leak(capsys):
    assert q.send(SECRET, ["x"]) is False        # keine URL -> Request wirft, wird abgefangen
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
