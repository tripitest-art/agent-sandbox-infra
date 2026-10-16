"""Tests für sandbox/dashboard_notify.py (Push über ntfy), ohne Netzwerk."""

import importlib.util
import sys
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "dashboard_notify.py"
_spec = importlib.util.spec_from_file_location("dashboard_notify", _PATH)
n = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_notify"] = n
_spec.loader.exec_module(n)

NOW = datetime(2026, 10, 16, 12, 0, 0)
URL = "https://ntfy.example/geheimes-topic-xyz"


def _state(**kw):
    base = {"slots": [], "budgets": {}, "veraltet": False}
    base.update(kw)
    return base


class _Resp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --- Notifier ----------------------------------------------------------------------------------

def test_notifier_ist_ohne_gueltige_url_aus():
    assert not n.Notifier(env={}).enabled
    assert not n.Notifier(env={"NTFY_URL": "ftp://x"}).enabled
    assert not n.Notifier(env={"NTFY_URL": "geheimes-topic"}).enabled
    assert n.Notifier(env={"NTFY_URL": URL}).enabled


def test_send_baut_anfrage_mit_utf8_titel_und_token():
    seen = []

    def opener(req, timeout):
        seen.append((req.full_url, req.get_method(), dict(req.header_items()), req.data))
        return _Resp()

    note = n.Notifier(env={"NTFY_URL": URL, "NTFY_TOKEN": "tk_abc"}, opener=opener)
    assert note.send(n.Event("k", "⚠ Stephan: Test", "Umlaut äöü", "high", "rotating_light"))
    url, method, headers, body = seen[0]
    assert url == URL and method == "POST" and body == "Umlaut äöü".encode("utf-8")
    assert headers["Title"].startswith("=?UTF-8?B?") and headers["Title"].isascii()
    assert headers["Priority"] == "high" and headers["Authorization"] == "Bearer tk_abc"


def test_send_ascii_titel_bleibt_lesbar_und_ohne_token_kein_auth_header():
    seen = []
    note = n.Notifier(env={"NTFY_URL": URL}, opener=lambda r, timeout: seen.append(dict(r.header_items())) or _Resp())
    note.send(n.Event("k", "Slot 1: tot", "x"))
    assert seen[0]["Title"] == "Slot 1: tot" and "Authorization" not in seen[0]


def test_send_fehler_nennen_nur_die_art_nie_url_oder_token():
    def boom(req, timeout):
        raise OSError(f"{URL} tk_abc")

    note = n.Notifier(env={"NTFY_URL": URL, "NTFY_TOKEN": "tk_abc"}, opener=boom)
    assert note.send(n.Event("k", "t", "m")) is False
    assert note.last_error == "OSError"

    def http(req, timeout):
        raise urllib.error.HTTPError(URL, 403, "tk_abc", {}, None)

    note.opener = http
    assert note.send(n.Event("k", "t", "m")) is False
    assert note.last_error == "HTTP 403"

    note.opener = lambda r, timeout: _Resp(500)
    assert note.send(n.Event("k", "t", "m")) is False and note.last_error == "HTTP 500"


def test_send_kuerzt_lange_nachrichten():
    seen = []
    note = n.Notifier(env={"NTFY_URL": URL}, opener=lambda r, timeout: seen.append(r.data) or _Resp())
    note.send(n.Event("k", "t", "x" * 5000))
    assert len(seen[0]) == n.MAX_BODY


# --- Watcher -----------------------------------------------------------------------------------

def test_stephan_zeilen_werden_in_einer_meldung_gebuendelt():
    w = n.Watcher()
    lines = [f"[w1] ⚠ Stephan: issue{i}: Fehler" for i in range(8)]
    (ev,) = w.events(_state(), lines, NOW)
    assert ev.priority == "high" and ev.message.count("\n") == n.MAX_LINES   # 5 Zeilen + „+3 weitere“
    assert ev.message.endswith("(+3 weitere)") and "issue7" in ev.message and "issue0" not in ev.message
    assert w.events(_state(), [], NOW) == []


def test_toter_slot_wird_einmal_gemeldet_und_nach_erholung_wieder():
    w = n.Watcher()
    tot = _state(slots=[{"slot": "2", "tot": True, "project": "sc-digger", "issue": 61}])
    (ev,) = w.events(tot, [], NOW)
    assert ev.title == "Slot 2: Prozess beendet" and "sc-digger Issue #61" in ev.message
    assert w.events(tot, [], NOW) == []                                  # läuft weiter: keine Wiederholung
    assert w.events(_state(slots=[{"slot": "2", "tot": False}]), [], NOW) == []
    assert len(w.events(tot, [], NOW)) == 1                              # erneut tot: erneut melden


def test_budget_meldet_ab_schwelle_einmal_je_key_und_tag():
    w = n.Watcher()
    st = _state(budgets={"worker": {"spend": 9.5, "max": 10, "prozent": 95},
                         "reviewer": {"spend": 1, "max": 10, "prozent": 10},
                         "planer": {"fehler": "nicht erreichbar"},
                         "x": {"spend": 1, "max": None, "prozent": None}})
    (ev,) = w.events(st, [], NOW)
    assert ev.title == "Budget worker: 95 %" and ev.message == "$9.5 von $10 verbraucht."
    assert w.events(st, [], NOW + timedelta(hours=5)) == []
    assert len(w.events(st, [], NOW + timedelta(days=1))) == 1           # neuer Tag
    assert len(w.budget_sent) == 1                                       # alter Tag vergessen


def test_veraltet_meldet_erst_nach_der_wartezeit_und_nur_einmal():
    w = n.Watcher(stale_after_s=600)
    st = _state(veraltet=True)
    assert w.events(st, [], NOW) == []
    assert w.events(st, [], NOW + timedelta(seconds=599)) == []
    (ev,) = w.events(st, [], NOW + timedelta(seconds=600))
    assert ev.key == "veraltet"
    assert w.events(st, [], NOW + timedelta(hours=1)) == []
    assert w.events(_state(), [], NOW + timedelta(hours=2)) == []         # erholt: zurücksetzen
    assert w.events(st, [], NOW + timedelta(hours=3)) == []               # Zähler beginnt neu
    assert len(w.events(st, [], NOW + timedelta(hours=3, minutes=10))) == 1


# --- NotifyLoop --------------------------------------------------------------------------------

class _LogBuf:
    def __init__(self):
        self.lines, self.seq = [], 0

    def add(self, text):
        self.seq += 1
        self.lines.append((self.seq, text))

    def poll(self):
        pass

    def since(self, seq):
        return [(i, t) for i, t in self.lines if i > seq]


class _Dash:
    def __init__(self):
        self.logbuf, self.current = _LogBuf(), _state()

    def state(self):
        return self.current


class _Note:
    def __init__(self):
        self.sent, self.ok, self.last_error = [], True, "OSError"

    def send(self, event):
        if self.ok:
            self.sent.append(event)
        return self.ok


def test_loop_meldet_nur_neue_stephan_zeilen():
    dash, note = _Dash(), _Note()
    dash.logbuf.add("alt ⚠ Stephan: issue1: alt")          # vor dem Start: nicht nachmelden
    loop = n.NotifyLoop(dash, note, clock=lambda: NOW)
    loop.step()
    assert note.sent == []
    dash.logbuf.add("normale Zeile")
    dash.logbuf.add("[w1] ⚠ Stephan: issue2: neu")
    loop.step()
    assert [e.key for e in note.sent] == ["stephan"] and "issue2" in note.sent[0].message
    loop.step()
    assert len(note.sent) == 1


def test_loop_wiederholt_nicht_zugestellte_meldungen(capsys):
    dash, note = _Dash(), _Note()
    loop = n.NotifyLoop(dash, note, clock=lambda: NOW)
    dash.logbuf.add("⚠ Stephan: issue2: neu")
    note.ok = False
    loop.step()
    loop.step()
    assert note.sent == [] and len(loop.queue) == 1
    err = capsys.readouterr().err
    assert err.count("Push fehlgeschlagen") == 1 and "OSError" in err      # nur der Wechsel steht im Journal
    note.ok = True
    loop.step()
    assert len(note.sent) == 1 and loop.queue == [] and not loop.failing


def test_loop_begrenzt_die_warteschlange():
    dash, note = _Dash(), _Note()
    note.ok = False
    loop = n.NotifyLoop(dash, note, clock=lambda: NOW)
    for i in range(30):
        dash.current = _state(slots=[{"slot": str(i), "tot": True}])
        loop.step()
    assert len(loop.queue) == n.MAX_QUEUE and loop.queue[-1].key == "slot-29"


def test_send_test_rueckgabewerte(capsys):
    assert n.send_test(n.Notifier(env={})) == 2
    ok = n.Notifier(env={"NTFY_URL": URL}, opener=lambda r, timeout: _Resp())
    assert n.send_test(ok) == 0
    bad = n.Notifier(env={"NTFY_URL": URL}, opener=lambda r, timeout: (_ for _ in ()).throw(OSError(URL)))
    assert n.send_test(bad) == 1
    out = capsys.readouterr()
    assert URL not in out.out + out.err
