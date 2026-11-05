import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "dashboard_notify.py"
_spec = importlib.util.spec_from_file_location("dashboard_notify_herzschlag", _PATH)
n = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_notify_herzschlag"] = n
_spec.loader.exec_module(n)

NOW = datetime(2026, 11, 5, 12, 0, 0)


def _state(bereit=2, pausiert=False):
    return {"slots": [], "budgets": {}, "veraltet": False, "pausiert": pausiert,
            "projekte": [{"name": "agent-sandbox-infra", "issues": {"bereit": bereit, "in-arbeit": 0}},
                         {"name": "sc-digger", "issues": {"in-arbeit": 1}}]}


def _utc(local_naive):
    return local_naive.astimezone().astimezone(timezone.utc).isoformat()


def test_konstanten():
    assert n.HEARTBEAT_AFTER_S == 4 * 3600
    assert n.AKTIV_EVENTS == frozenset({"run_started", "run_finished", "agent_run_finished",
                                        "pr_opened", "review_posted", "pr_merged"})


def test_letzte_aktivitaet_nimmt_das_juengste_passende_ereignis(tmp_path):
    log = tmp_path / "audit.jsonl"
    zeilen = [
        {"ts": _utc(NOW - timedelta(hours=9)), "event": "run_started"},
        {"ts": _utc(NOW - timedelta(hours=6)), "event": "review_posted"},
        {"ts": _utc(NOW - timedelta(hours=1)), "event": "stephan_alarm"},
        {"event": "pr_merged"},
        {"ts": "kaputt", "event": "pr_merged"},
    ]
    text = "\n".join(json.dumps(z) for z in zeilen) + "\nkein json\n[1, 2]\n"
    log.write_text(text, encoding="utf-8")
    assert n.letzte_aktivitaet(str(log)) == NOW - timedelta(hours=6)


def test_letzte_aktivitaet_naive_zeit_und_fehlende_datei(tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text(json.dumps({"ts": "2026-11-05T07:30:00", "event": "pr_merged"}) + "\n", encoding="utf-8")
    assert n.letzte_aktivitaet(str(log)) == datetime(2026, 11, 5, 7, 30)
    assert n.letzte_aktivitaet(str(tmp_path / "fehlt.jsonl")) is None
    leer = tmp_path / "leer.jsonl"
    leer.write_text(json.dumps({"ts": "2026-11-05T07:30:00", "event": "stephan_alarm"}) + "\n", encoding="utf-8")
    assert n.letzte_aktivitaet(str(leer)) is None


def test_letzte_aktivitaet_liest_nur_das_ende(tmp_path):
    log = tmp_path / "audit.jsonl"
    alt = json.dumps({"ts": "2026-11-05T11:00:00", "event": "pr_merged", "x": "y" * 300})
    neu = json.dumps({"ts": "2026-11-05T08:00:00", "event": "run_started"})
    log.write_text(alt + "\n" + neu + "\n", encoding="utf-8")
    assert n.letzte_aktivitaet(str(log), max_bytes=len(neu) + 20) == datetime(2026, 11, 5, 8, 0)


def test_herzschlag_meldet_einmal_je_stillstand():
    wa = n.Watcher()
    letzte = NOW - timedelta(hours=5, minutes=30)
    ev = wa.herzschlag(_state(bereit=2), letzte, NOW)
    assert ev is not None
    assert ev.key == "herzschlag" and ev.title == "⚠ Stephan: Pipeline steht"
    assert ev.message == "Seit 5 h kein Agentenlauf, kein Review und kein Merge, obwohl 2 Issues bereit sind."
    assert ev.priority == "high" and ev.tags == "zzz"
    assert wa.herzschlag(_state(bereit=2), letzte, NOW + timedelta(hours=1)) is None
    wieder = NOW + timedelta(hours=1)
    assert wa.herzschlag(_state(bereit=2), wieder, wieder + timedelta(minutes=5)) is None
    spaeter = wieder + timedelta(hours=4)
    assert wa.herzschlag(_state(bereit=2), wieder, spaeter) is not None


def test_herzschlag_schweigt_ohne_grund():
    wa = n.Watcher()
    assert wa.herzschlag(_state(bereit=0), NOW - timedelta(hours=10), NOW) is None
    assert wa.herzschlag(_state(bereit=3), NOW - timedelta(hours=3, minutes=59), NOW) is None
    assert wa.herzschlag(_state(bereit=3), None, NOW) is None
    assert wa.herzschlag({}, NOW - timedelta(hours=10), NOW) is None


def test_herzschlag_nennt_die_pause():
    ev = n.Watcher(heartbeat_after_s=3600).herzschlag(_state(bereit=1, pausiert=True),
                                                      NOW - timedelta(hours=2), NOW)
    assert ev.message.endswith(" Die Pipeline ist pausiert (Dashboard).")
    assert ev.message.startswith("Seit 2 h ")


class _LogBuf:
    seq = 0

    def poll(self):
        pass

    def since(self, seq):
        return []


class _Dash:
    def __init__(self, state):
        self.logbuf, self.current, self.aufrufe = _LogBuf(), state, 0

    def state(self):
        self.aufrufe += 1
        return self.current


class _Note:
    last_error = ""

    def __init__(self):
        self.sent = []

    def send(self, event):
        self.sent.append(event)
        return True


def test_notify_loop_schickt_den_herzschlag(tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text(json.dumps({"ts": _utc(NOW - timedelta(hours=5)), "event": "run_finished"}) + "\n",
                   encoding="utf-8")
    dash, note = _Dash(_state(bereit=2)), _Note()
    loop = n.NotifyLoop(dash, note, clock=lambda: NOW, audit_path=str(log))
    loop.step()
    assert [e.key for e in note.sent] == ["herzschlag"]
    assert dash.aufrufe == 1
    loop.step()
    assert [e.key for e in note.sent] == ["herzschlag"]


def test_notify_loop_ohne_audit_log_bleibt_still(tmp_path):
    dash, note = _Dash(_state(bereit=2)), _Note()
    loop = n.NotifyLoop(dash, note, clock=lambda: NOW, audit_path=str(tmp_path / "fehlt.jsonl"))
    loop.step()
    assert note.sent == []
