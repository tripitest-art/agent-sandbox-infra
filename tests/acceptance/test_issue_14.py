import importlib.util
import json
import os
import sys
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load("worker_tick")
q = _load("qwen_webhook")


def test_write_slot_status_idle(tmp_path):
    w.write_slot_status("idle", slot="1", message="Warte auf nächsten Takt", slot_dir=str(tmp_path))
    sf = tmp_path / "slot-1.json"
    assert sf.exists()
    data = json.loads(sf.read_text(encoding="utf-8"))
    assert data["slot"] == "1"
    assert data["status"] == "idle"
    assert data["message"] == "Warte auf nächsten Takt"


def test_write_slot_status_running(tmp_path):
    w.write_slot_status("running", slot="2", issue=14, title="Slot-Telemetrie",
                        branch="feature/issue-14", model="coder", agent="qwen-code",
                        started_at="17:20:00", pid=12345, slot_dir=str(tmp_path))
    sf = tmp_path / "slot-2.json"
    assert sf.exists()
    data = json.loads(sf.read_text(encoding="utf-8"))
    assert data["slot"] == "2"
    assert data["status"] == "running"
    assert data["issue"] == 14
    assert data["title"] == "Slot-Telemetrie"
    assert data["pid"] == 12345


def test_qwen_webhook_read_slots(tmp_path):
    (tmp_path / "slot-1.json").write_text(json.dumps({"slot": "1", "status": "running", "pid": 100}), encoding="utf-8")
    (tmp_path / "slot-2.json").write_text(json.dumps({"slot": "2", "status": "idle"}), encoding="utf-8")
    slots = q.read_slots(str(tmp_path), is_pid_alive=lambda pid: True)
    assert "1" in slots and "2" in slots
    assert slots["1"]["status"] == "running"
    assert slots["2"]["status"] == "idle"


def test_qwen_webhook_detects_dead_pid(tmp_path):
    (tmp_path / "slot-1.json").write_text(json.dumps({"slot": "1", "status": "running", "pid": 99999}), encoding="utf-8")
    slots = q.read_slots(str(tmp_path), is_pid_alive=lambda pid: False)
    assert slots["1"]["status"] == "idle"
    assert slots["1"]["message"] == "Prozess beendet"


def test_qwen_webhook_payload_with_slots():
    data = json.loads(q.payload(["line1", "line2"], slots={"1": {"status": "idle"}}))
    assert data == {"lines": "line1\nline2", "slots": {"1": {"status": "idle"}}}


def test_qwen_webhook_step_triggers_on_slot_change(tmp_path):
    (tmp_path / "slot-1.json").write_text(json.dumps({"slot": "1", "status": "running", "pid": 100}), encoding="utf-8")
    sent = []

    class FakeFollower:
        def poll(self): return []

    def fake_send(url, lines, slots=None):
        sent.append((lines, slots))
        return True

    # Schritt 1: last_slots leer -> sendet sofort wegen Slot-Änderung, obwohl lines leer
    pending, last_slots = q.step(FakeFollower(), [], "http://ha.test", last_slots={}, slot_dir=str(tmp_path), sender=fake_send, is_pid_alive=lambda pid: True)
    assert len(sent) == 1
    assert sent[0][0] == []
    assert sent[0][1]["1"]["status"] == "running"
    assert pending == []

    # Schritt 2: keine neuen Zeilen und Slots unverändert -> kein Senden
    pending, last_slots = q.step(FakeFollower(), pending, "http://ha.test", last_slots=last_slots, slot_dir=str(tmp_path), sender=fake_send, is_pid_alive=lambda pid: True)
    assert len(sent) == 1
