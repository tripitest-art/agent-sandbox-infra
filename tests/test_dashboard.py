"""Tests für sandbox/dashboard.py (Web-Dashboard), ohne Netzwerk, ohne gh, ohne Server."""

import importlib.util
import json
import sys
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
_spec = importlib.util.spec_from_file_location("dashboard", _SANDBOX / "dashboard.py")
d = importlib.util.module_from_spec(_spec)
sys.modules["dashboard"] = d
_spec.loader.exec_module(d)

NOW = datetime(2026, 10, 16, 12, 0, 0)

STATUS = {
    "zeit": "2026-10-16T10:00:00+00:00",
    "projekte": [
        {"name": "a", "repo": "o/a", "issues": {"bereit": 2, "in-arbeit": 1, "blockiert": 0, "entwurf": 1},
         "prs": {"offen": 3, "rot": 1, "laufend": 0,
                 "wartet_auf_stephan": [{"number": 7, "title": "T", "url": "https://x/7"}]}},
        {"name": "b", "repo": "o/b", "fehler": "CalledProcessError"},
    ],
    "braucht_stephan": 1,
    "budgets": {"worker": {"spend": 1.5, "max": 10, "prozent": 15}, "reviewer": {"fehler": "nicht erreichbar"}},
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    # Kein Test liest die echten Pfade des Servers; Netz und gh kommen nie ins Spiel, weil
    # der Statusbau (`default_builder`) in jedem Test durch einen Fake ersetzt wird.
    monkeypatch.setattr(d, "LOG", str(tmp_path / "worker.log"))
    monkeypatch.setattr(d, "SLOT_DIR", str(tmp_path / "slots"))


def _slot(tmp_path, n, **data):
    (tmp_path / "slots").mkdir(exist_ok=True)
    (tmp_path / "slots" / f"slot-{n}.json").write_text(json.dumps({"slot": str(n), **data}), encoding="utf-8")


def _dash(tmp_path, builder=lambda: STATUS, alive=lambda pid: True):
    log = tmp_path / "worker.log"
    log.touch()
    return d.Dashboard(d.StatusCache(builder, ttl=60), d.LogBuffer(str(log)),
                       slot_dir=str(tmp_path / "slots"), is_pid_alive=alive, clock=lambda: NOW)


def test_tote_pid_wird_idle_und_loest_banner_aus(tmp_path):
    _slot(tmp_path, 1, status="running", pid=99, started_at="11:00:00", project="a")
    state = _dash(tmp_path, alive=lambda pid: False).state()
    slot = state["slots"][0]
    assert slot["status"] == "idle" and slot["tot"] is True
    assert "laufzeit_s" not in slot
    assert state["kopf"]["aktive_slots"] == 0
    assert any(a["art"] == "slot" and "Slot 1" in a["text"] for a in state["alerts"])


def test_laufender_slot_hat_laufzeit_und_zaehlt_als_aktiv(tmp_path):
    _slot(tmp_path, 2, status="running", pid=1, started_at="11:30:00", model="m", issue=5)
    state = _dash(tmp_path).state()
    assert state["slots"][0]["laufzeit_s"] == 1800
    assert state["kopf"]["aktive_slots"] == 1
    assert state["alerts"] == []


def test_laufzeit_ueber_mitternacht():
    assert d._elapsed("23:00:00", datetime(2026, 10, 16, 1, 0, 0)) == 2 * 3600
    assert d._elapsed("kaputt", NOW) is None
    assert d._elapsed(None, NOW) is None


def test_slots_numerisch_sortiert_und_unbekannter_status(tmp_path):
    for n in (10, 2):
        _slot(tmp_path, n, status="idle")
    _slot(tmp_path, 3, status="waiting_user")
    slots = _dash(tmp_path).state()["slots"]
    assert [s["slot"] for s in slots] == ["2", "3", "10"]
    assert slots[1]["status"] == "unbekannt"


def test_defekte_slot_datei_wird_ignoriert(tmp_path):
    (tmp_path / "slots").mkdir()
    (tmp_path / "slots" / "slot-1.json").write_text("{kaputt", encoding="utf-8")
    assert _dash(tmp_path).state()["slots"] == []


def test_kopfzahlen_und_projekte_aus_status(tmp_path):
    state = _dash(tmp_path).state()
    assert state["kopf"]["offene_issues"] == 4 and state["kopf"]["offene_prs"] == 3
    assert state["braucht_stephan"] == 1
    assert state["projekte"][1]["fehler"] == "CalledProcessError"
    assert state["budgets"]["reviewer"] == {"fehler": "nicht erreichbar"}
    assert state["veraltet"] is False


def test_status_cache_nutzt_ttl_und_behaelt_alten_stand_bei_fehler():
    calls, now, fail = [], [0.0], [False]

    def builder():
        calls.append(1)
        if fail[0]:
            raise RuntimeError("gh weg")
        return {"n": len(calls)}

    c = d.StatusCache(builder, ttl=90, clock=lambda: now[0])
    assert c.get() == ({"n": 1}, False)
    now[0] = 30
    assert c.get() == ({"n": 1}, False) and len(calls) == 1
    now[0], fail[0] = 100, True
    assert c.get() == ({"n": 1}, True)          # Fehler: alter Stand, als veraltet markiert
    now[0], fail[0] = 200, False
    assert c.get() == ({"n": 3}, False)


def test_ohne_jemals_erfolgreichen_status_bleibt_dashboard_nutzbar(tmp_path):
    def boom():
        raise RuntimeError("geheim-token-123")

    state = _dash(tmp_path, builder=boom).state()
    assert state["veraltet"] is True and state["projekte"] == []
    assert "geheim-token-123" not in json.dumps(state)


def test_redact_maskiert_geheimwerte_und_ansi():
    env = {"LITELLM_WORKER_KEY": "sk-supersecret-1", "HA_WEBHOOK_URL": "abcdefghijklmnop1234",
           "PATH": "/usr/bin/xxxxxxxxxx", "KURZ_KEY": "abc"}
    text = d.redact("\x1b[31mfehler\x1b[0m sk-supersecret-1 via abcdefghijklmnop1234 /usr/bin/xxxxxxxxxx abc", env)
    assert "sk-supersecret-1" not in text and "abcdefghijklmnop1234" not in text
    assert "<LITELLM_WORKER_KEY>" in text and "\x1b" not in text
    assert "/usr/bin/xxxxxxxxxx" in text and text.endswith(" abc")   # kurze/unverdächtige Werte bleiben


def test_logbuffer_preload_nachlesen_und_stephan_filter(tmp_path):
    log = tmp_path / "worker.log"
    stephan = "2026 [w1] ⚠ Stephan: issue1: Fehler"
    log.write_bytes(f"alt\n{stephan}\n".encode("utf-8"))      # LF auch unter Windows
    buf = d.LogBuffer(str(log), limit=3)
    assert [ln for _, ln in buf.since(0)] == ["alt", stephan]
    with open(log, "ab") as fh:
        fh.write(b"neu 1\nneu 2\n")
    buf.poll()
    assert [ln for _, ln in buf.since(0)] == [stephan, "neu 1", "neu 2"]   # limit=3 verdrängt „alt“
    assert buf.stephan() == [stephan]
    assert [ln for _, ln in buf.since(2)]   # Nummern: alt=1, stephan=2 == ["neu 1", "neu 2"]


def test_logbuffer_ohne_datei_ist_leer(tmp_path):
    buf = d.LogBuffer(str(tmp_path / "gibts-nicht.log"))
    buf.poll()
    assert buf.since(0) == []


def test_sse_frames_senden_nur_bei_aenderung(tmp_path):
    log = tmp_path / "worker.log"
    dash = _dash(tmp_path)
    frames, seq, last = dash.frames(0, None)
    assert len(frames) == 1 and frames[0].startswith("event: state\ndata: {")
    frames, seq, last = dash.frames(seq, last)
    assert frames == []                               # nichts geändert: kein Rahmen
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("zeile 1\n")
    dash.logbuf.poll()
    frames, seq, last = dash.frames(seq, last)
    assert frames == ['event: log\ndata: ["zeile 1"]\n\n']
    assert dash.frames(seq, last)[0] == []


def test_http_endpunkte(tmp_path):
    dash = _dash(tmp_path)
    server = d.ThreadingHTTPServer(("127.0.0.1", 0), d.make_handler(dash, interval=0.01))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert urllib.request.urlopen(base + "/healthz", timeout=5).read() == b"ok"
        assert b"EventSource" in urllib.request.urlopen(base + "/", timeout=5).read()
        state = json.load(urllib.request.urlopen(base + "/api/state", timeout=5))
        assert state["kopf"]["offene_prs"] == 3
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/nix", timeout=5)
        assert err.value.code == 404
        with urllib.request.urlopen(base + "/events", timeout=5) as resp:
            assert resp.headers["Content-Type"] == "text/event-stream"
            assert resp.readline() == b"event: state\n"
    finally:
        server.shutdown()
        server.server_close()
