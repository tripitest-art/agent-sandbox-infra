"""Tests für sandbox/dashboard.py (Web-Dashboard), ohne Netzwerk, ohne gh, ohne Server."""

import importlib.util
import json
import sys
import threading
import urllib.error
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


# --- Budget ändern -----------------------------------------------------------------------------

TOKEN = "ein-langer-schreib-token"
ENV = {"DASHBOARD_WRITE_TOKEN": TOKEN, "LITELLM_MASTER_KEY": "sk-master-geheim",
       "LITELLM_WORKER_KEY": "sk-worker-geheim", "LITELLM_REVIEW_KEY": "sk-review-geheim"}


def _writer(calls, env=None):
    w = d.BudgetWriter(env=ENV if env is None else env, update=lambda key, v: calls.append((key, v)))
    w.fail_delay = 0
    return w


def test_writer_ist_ohne_master_key_oder_kurzen_token_aus():
    assert _writer([]).enabled
    assert not _writer([], {**ENV, "LITELLM_MASTER_KEY": ""}).enabled
    assert not _writer([], {**ENV, "DASHBOARD_WRITE_TOKEN": "kurz"}).enabled
    assert not _writer([], {}).enabled
    assert not _writer([], {**ENV, "DASHBOARD_WRITE_TOKEN": "kurz"}).token_ok("kurz")


@pytest.mark.parametrize("value", [True, "5", None, float("nan"), float("inf"), 0, -3, 10001, [5]])
def test_writer_lehnt_ungueltige_limits_ab(value):
    calls = []
    with pytest.raises(d.BudgetError):
        _writer(calls).set_budget("worker", value)
    assert calls == []


def test_writer_lehnt_unbekannten_key_ab_und_rundet():
    calls = []
    w = _writer(calls)
    for alias in ("planer", "gibts-nicht", None, 3):
        with pytest.raises(d.BudgetError):
            w.set_budget(alias, 5)          # planer: kein Key in der Umgebung
    assert w.set_budget("worker", 12.3456) == 12.35
    assert calls == [("sk-worker-geheim", 12.35)]


def test_litellm_update_sendet_master_key_und_meldet_fehler_ohne_geheimnisse(monkeypatch):
    sent = []

    class Resp:
        def close(self):
            pass

    def ok(req, timeout):
        sent.append((req.full_url, req.headers["Authorization"], json.loads(req.data)))
        return Resp()

    w = d.BudgetWriter(env={**ENV, "LITELLM": "http://llm.test:4000/"})
    monkeypatch.setattr(d.urllib.request, "urlopen", ok)
    w._litellm_update("sk-worker-geheim", 7.5)
    assert sent == [("http://llm.test:4000/key/update", "Bearer sk-master-geheim",
                     {"key": "sk-worker-geheim", "max_budget": 7.5})]

    def http_fehler(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "sk-master-geheim", {}, None)

    monkeypatch.setattr(d.urllib.request, "urlopen", http_fehler)
    with pytest.raises(d.BudgetError) as err:
        w._litellm_update("sk-worker-geheim", 7.5)
    assert "401" in str(err.value) and "sk-" not in str(err.value) and "llm.test" not in str(err.value)

    def netz(req, timeout):
        raise OSError("sk-master-geheim llm.test")

    monkeypatch.setattr(d.urllib.request, "urlopen", netz)
    with pytest.raises(d.BudgetError) as err:
        w._litellm_update("sk-worker-geheim", 7.5)
    assert str(err.value) == "LiteLLM nicht erreichbar (OSError)"


def test_set_budget_laedt_den_status_neu(tmp_path):
    loads = []
    dash = _dash(tmp_path, builder=lambda: loads.append(1) or STATUS)
    dash.writer = _writer([])
    dash.state()
    dash.state()
    assert len(loads) == 1
    dash.set_budget("worker", 20)
    dash.state()
    assert len(loads) == 2


def test_state_meldet_ob_aendern_moeglich_ist(tmp_path):
    dash = _dash(tmp_path)
    assert dash.state()["schreiben"] is False
    dash.writer = _writer([])
    assert dash.state()["schreiben"] is True


def _post(base, payload, token=TOKEN, raw=None):
    data = raw if raw is not None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Dashboard-Token"] = token
    req = urllib.request.Request(base + "/api/budget", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


@pytest.fixture
def served(tmp_path):
    calls = []
    dash = _dash(tmp_path)
    dash.writer = _writer(calls)
    server = d.ThreadingHTTPServer(("127.0.0.1", 0), d.make_handler(dash, interval=0.01))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", calls, dash
    server.shutdown()
    server.server_close()


def test_post_budget_mit_richtigem_token(served):
    base, calls, _ = served
    status, body = _post(base, {"alias": "worker", "max_budget": 15})
    assert status == 200 and body == {"ok": True, "alias": "worker", "max_budget": 15.0}
    assert calls == [("sk-worker-geheim", 15.0)]


def test_post_budget_ohne_oder_mit_falschem_token_aendert_nichts(served):
    base, calls, _ = served
    for token in (None, "", "falsch", TOKEN[:-1]):
        status, body = _post(base, {"alias": "worker", "max_budget": 15}, token=token)
        assert status == 403 and body == {"fehler": "Token falsch"}
    assert calls == []


def test_post_budget_ohne_einrichtung_ist_gesperrt(served):
    base, calls, dash = served
    dash.writer = d.BudgetWriter(env={})
    status, body = _post(base, {"alias": "worker", "max_budget": 15})
    assert status == 403 and "nicht eingerichtet" in body["fehler"]
    assert calls == []


def test_post_budget_fehlerfaelle(served):
    base, calls, _ = served
    assert _post(base, None, raw=b"kein json")[0] == 400
    assert _post(base, None, raw=b"[1]")[0] == 400
    assert _post(base, None, raw=b"")[0] == 400
    assert _post(base, None, raw=b"x" * 2000)[0] == 400
    assert _post(base, {"alias": "worker", "max_budget": -1})[0] == 400
    assert _post(base, {"alias": "worker", "max_budget": "9"})[0] == 400
    status, body = _post(base, {"alias": "planer", "max_budget": 5})
    assert status == 400 and body == {"fehler": "Unbekannter Key"}
    assert calls == []


def test_post_auf_anderen_pfad_ist_404(served):
    base = served[0]
    req = urllib.request.Request(base + "/api/state", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 404


def test_antworten_und_state_enthalten_keine_geheimnisse(served):
    base, _, dash = served
    texte = [json.dumps(dash.state()), json.dumps(_post(base, {"alias": "x", "max_budget": 1})[1]),
             json.dumps(_post(base, {"alias": "worker", "max_budget": 1}, token="falsch")[1]),
             urllib.request.urlopen(base + "/", timeout=5).read().decode()]
    for text in texte:
        for geheim in (TOKEN, "sk-master-geheim", "sk-worker-geheim", "sk-review-geheim"):
            assert geheim not in text


# --- Push (dashboard_notify) --------------------------------------------------------------------

def test_main_notify_test_ohne_url_endet_mit_2(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.setattr(d.dashboard_notify, "ENV_FILE", str(tmp_path / "fehlt"))
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--notify-test"])
    with pytest.raises(SystemExit) as exit_info:
        d.main()
    assert exit_info.value.code == 2
    assert "NTFY_URL" in capsys.readouterr().err


def test_main_notify_test_liest_die_env_datei_und_sendet(monkeypatch, tmp_path, capsys):
    env = tmp_path / "dashboard.env"
    env.write_text("NTFY_URL=https://ntfy.example/topic\n", encoding="utf-8")
    seen = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(request, timeout):
        seen.append(request.full_url)
        return Resp()

    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.setattr(d.dashboard_notify, "ENV_FILE", str(env))
    real = d.dashboard_notify.Notifier
    monkeypatch.setattr(d.dashboard_notify, "Notifier", lambda env=None: real(env=env, opener=opener))
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--notify-test"])
    with pytest.raises(SystemExit) as exit_info:
        d.main()
    assert exit_info.value.code == 0
    assert seen == ["https://ntfy.example/topic"]
    assert "Testmeldung gesendet" in capsys.readouterr().out


def test_serve_startet_den_push_faden_nur_mit_url(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(d.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda self: started.append(args)})())
    monkeypatch.delenv("NTFY_URL", raising=False)
    server = d.serve("127.0.0.1", 0)
    assert server.push is False and started == []
    server.server_close()
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example/topic-abc")
    server = d.serve("127.0.0.1", 0)
    assert server.push is True and started == [(d.NOTIFY_INTERVAL,)]
    server.server_close()


def test_strip_ansi_entfernt_escape_sequenzen():
    assert d.strip_ansi("\x1b[31mRot\x1b[0m und normal") == "Rot und normal"
    assert d.strip_ansi("Kein ANSI") == "Kein ANSI"


def test_pid_is_active_ungueltige_pids():
    assert d.pid_is_active(0) is False
    assert d.pid_is_active(-1) is False
    assert d.pid_is_active(None) is False
    assert d.pid_is_active(d.os.getpid()) is True


def test_read_slots_verwaist_und_aktiv(tmp_path):
    s1 = tmp_path / "slot-1.json"
    s1.write_text(json.dumps({"slot": "1", "status": "running", "pid": 999999}), encoding="utf-8")
    s2 = tmp_path / "slot-2.json"
    s2.write_text(json.dumps({"slot": "2", "status": "idle"}), encoding="utf-8")

    res = d.read_slots(str(tmp_path), is_pid_alive=lambda pid: False)
    assert res["1"]["status"] == "idle"
    assert res["1"]["message"] == d.DEAD_MESSAGE
    assert res["2"]["status"] == "idle"


def test_summarize_project_und_budget():
    p = d.projects.Project(name="proj", repo="org/proj", workdir="/root/proj")
    issues = [{"number": 1, "labels": [{"name": "bereit"}, {"name": "worker-task"}]}]
    prs = [{"number": 2, "title": "PR", "url": "https://...", "labels": [{"name": "braucht-stephan"}],
            "statusCheckRollup": [{"name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"}]}]
    summary = d.summarize_project(p, issues, prs)
    assert summary["name"] == "proj"
    assert summary["issues"]["bereit"] == 1
    assert summary["prs"]["rot"] == 1
    assert len(summary["prs"]["wartet_auf_stephan"]) == 1

    b = d.summarize_budget({"spend": 2.5, "max_budget": 10.0})
    assert b["spend"] == 2.5
    assert b["max"] == 10.0
    assert b["prozent"] == 25
    assert d.summarize_budget(None) == {"fehler": "nicht erreichbar"}


def test_build_status_baut_vollstaendigen_zustand():
    p = d.projects.Project(name="proj", repo="org/proj", workdir="/root/proj")
    now = d.datetime(2026, 10, 18, 12, 0, 0, tzinfo=d.timezone.utc)
    fetch = lambda kind, repo: []
    budgets = {"worker": {"spend": 1.0, "max_budget": 10.0}}
    res = d.build_status([p], fetch, budgets, now)
    assert res["zeit"] == "2026-10-18T12:00:00+00:00"
    assert len(res["projekte"]) == 1
    assert res["budgets"]["worker"]["spend"] == 1.0

