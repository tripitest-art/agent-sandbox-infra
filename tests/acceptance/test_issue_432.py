import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "dashboard_notify.py"
_spec = importlib.util.spec_from_file_location("dashboard_notify_modelle", _PATH)
n = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_notify_modelle"] = n
_spec.loader.exec_module(n)

NOW = datetime(2026, 11, 5, 12, 0, 0)
ZEILE_403 = ("2026-11-05 13:57:39 [w2] issue453: kein Modell antwortet (coder-deepseek-free: HTTP 403; "
             "coder-mimo-free: HTTP 403), warte. Versuch zählt nicht.")


def _utc(local_naive):
    return local_naive.astimezone().astimezone(timezone.utc).isoformat()


def _lauf(minuten_vorher, slot, modell, event="agent_run_finished"):
    return {"ts": _utc(NOW - timedelta(minutes=minuten_vorher)), "event": event,
            "slot": slot, "active_model": modell}


def _schreibe(path, zeilen):
    path.write_text("".join(json.dumps(z) + "\n" for z in zeilen), encoding="utf-8")


def test_konstanten():
    assert n.BEZAHLTE_ALIASE == frozenset({"coder", "coder-or"})
    assert n.BEZAHLT_FENSTER_S == 3600


def test_bezahlte_slots_nur_wenn_alle_laeufe_bezahlt(tmp_path):
    log = tmp_path / "audit.jsonl"
    _schreibe(log, [
        _lauf(50, "1", "coder-or"), _lauf(10, "1", "coder-or"),
        _lauf(40, "2", "coder"), _lauf(5, "2", "coder-go"),          # Slot 2 lief zuletzt über Go
        _lauf(30, "3", "coder"), _lauf(20, "3", "coder-or"),
        _lauf(90, "4", "coder-or"),                                   # außerhalb des Fensters
        _lauf(15, "5", "coder-or", event="run_finished"),             # kein Agentenlauf
        {"ts": _utc(NOW - timedelta(minutes=5)), "event": "agent_run_finished", "active_model": "coder"},
    ])
    with log.open("a", encoding="utf-8") as fh:
        fh.write("kein json\n")
    assert n.bezahlte_slots(str(log), NOW) == {"1": "coder-or", "3": "coder-or"}


def test_bezahlte_slots_ohne_datei(tmp_path):
    assert n.bezahlte_slots(str(tmp_path / "fehlt.jsonl"), NOW) == {}


def test_bezahlt_meldet_einmal_je_tag():
    wa = n.Watcher()
    ev = wa.modelle({"1": "coder-or"}, [], NOW)
    assert [(e.key, e.title, e.message, e.priority, e.tags) for e in ev] == [(
        "bezahlt-1", "Slot 1: bezahltes Modell coder-or",
        "Alle Agentenläufe der letzten 60 Minuten liefen über coder-or (Guthaben statt Abo oder frei).",
        "default", "moneybag")]
    assert wa.modelle({"1": "coder-or"}, [], NOW + timedelta(hours=3)) == []
    assert [e.key for e in wa.modelle({"1": "coder"}, [], NOW + timedelta(hours=3))] == ["bezahlt-1"], \
        "anderes Modell am selben Tag meldet erneut"
    assert [e.key for e in wa.modelle({"1": "coder-or"}, [], NOW + timedelta(days=1))] == ["bezahlt-1"]


def test_modellausfall_aus_der_logzeile():
    wa = n.Watcher()
    ev = wa.modelle({}, ["2026-11-05 13:50:00 [w1] Keine Arbeit.", ZEILE_403], NOW)
    assert [(e.key, e.title, e.priority, e.tags) for e in ev] == [
        ("modelle-2", "Slot 2: kein Modell antwortet", "high", "no_entry")]
    assert ev[0].message == "coder-deepseek-free: HTTP 403; coder-mimo-free: HTTP 403"
    assert wa.modelle({}, [ZEILE_403], NOW + timedelta(hours=1)) == []
    assert [e.key for e in wa.modelle({}, [ZEILE_403], NOW + timedelta(days=1))] == ["modelle-2"]


def test_zeitueberschreitung_ist_kein_4xx():
    wa = n.Watcher()
    zeile = "2026-11-05 13:50:00 [w1] issue5: kein Modell antwortet (coder-go: TimeoutError), warte. Versuch zählt nicht."
    assert wa.modelle({}, [zeile], NOW) == []


def test_langer_grund_wird_gekuerzt():
    wa = n.Watcher()
    grund = "; ".join(f"coder-{i}-free: HTTP 403" for i in range(40))
    ev = wa.modelle({}, [f"2026-11-05 13:50:00 [w2] issue5: kein Modell antwortet ({grund}), warte."], NOW)
    assert ev[0].message == grund[:300]


class _LogBuf:
    def __init__(self, zeilen):
        self.zeilen, self.seq = zeilen, 0

    def poll(self):
        pass

    def since(self, seq):
        return [(i + 1, z) for i, z in enumerate(self.zeilen) if i + 1 > seq]


class _Dash:
    def __init__(self, zeilen):
        self.logbuf = _LogBuf(zeilen)

    def state(self):
        return {"slots": [], "budgets": {}, "veraltet": False, "projekte": []}


class _Note:
    last_error = ""

    def __init__(self):
        self.sent = []

    def send(self, event):
        self.sent.append(event)
        return True


def test_notify_loop_schickt_beide_meldungen(tmp_path):
    log = tmp_path / "audit.jsonl"
    _schreibe(log, [_lauf(10, "1", "coder-or")])
    dash, note = _Dash([]), _Note()
    loop = n.NotifyLoop(dash, note, clock=lambda: NOW, audit_path=str(log))
    dash.logbuf.zeilen = [ZEILE_403]
    loop.step()
    assert [e.key for e in note.sent] == ["bezahlt-1", "modelle-2"]
    loop.step()
    assert [e.key for e in note.sent] == ["bezahlt-1", "modelle-2"], "kein zweites Mal am selben Tag"
