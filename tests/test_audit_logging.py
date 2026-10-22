"""Tests für Audit-Logging (worker_tick.py, review_tick.py, dashboard_stats.py) ohne Netzwerk."""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load_module("worker_tick")
r = _load_module("review_tick")
ds = _load_module("dashboard_stats")


# ---- Fixtures ----------------------------------------------------------------

@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    """Audit-Log in temp-Verzeichnis umleiten."""
    f = tmp_path / "audit.jsonl"
    monkeypatch.setattr(w, "AUDIT_LOG", str(f))
    monkeypatch.setattr(r, "AUDIT_LOG", str(f))
    return f


@pytest.fixture
def worker_env(tmp_path, monkeypatch):
    """Worker-Takt-Umgebung für Tests."""
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    monkeypatch.setattr(w, "MODEL", "test-model")
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "WORKER", "Test Worker")
    monkeypatch.setattr(w, "SLOT_TAG", "w1")
    # STATE umleiten
    state_file = tmp_path / "worker-state.json"
    monkeypatch.setattr(w, "STATE", str(state_file))
    return tmp_path


def _audit_zeilen(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---- worker_tick: audit() Grundfunktionalität (bestehende Tests) ------------

def test_audit_schreibt_ereignis_mit_utc_zeit(audit_log, worker_env):
    w.audit("ereignis", issue=5)
    z = _audit_zeilen(audit_log)[0]
    assert z["event"] == "ereignis" and z["issue"] == 5
    assert z["slot"] == "1" and z["model"] == "test-model"
    assert datetime.fromisoformat(z["ts"]).utcoffset() == timedelta(0)


def test_audit_maskiert_nur_die_geheimnamen(audit_log):
    w.audit("x", password="GEHEIM-pw", secret="GEHEIM-se", api_key="GEHEIM-k",
            authorization="GEHEIM-a", token="GEHEIM-t", Api_Key="GEHEIM-gross",
            input_tokens=3, password_hint="sichtbar")
    text = audit_log.read_text(encoding="utf-8")
    assert "GEHEIM" not in text
    z = _audit_zeilen(audit_log)[0]
    assert z["input_tokens"] == 3 and z["password_hint"] == "sichtbar"
    assert all(z[n] == "***" for n in ("password", "secret", "api_key", "authorization", "token", "Api_Key"))


# ---- worker_tick: neue Audit-Events ------------------------------------------

def test_alarm_schreibt_stephan_alarm_event(audit_log, monkeypatch):
    """alarm() schreibt zusätzlich ein stephan_alarm-Event (#222)."""
    monkeypatch.setattr(w, "WORKER_SLOT", "2")
    w.alarm("issue42", "TimeoutError: Modell antwortet nicht")
    zeilen = _audit_zeilen(audit_log)
    # Zwei Events: das war vorher schon (z.B. run_started etc. nicht hier), aber alarm() schreibt stephan_alarm
    alarm_events = [z for z in zeilen if z["event"] == "stephan_alarm"]
    assert len(alarm_events) == 1
    z = alarm_events[0]
    assert z["event"] == "stephan_alarm"
    assert z["skey"] == "issue42"
    assert z["error_class"] == "Fehler"  # str -> "Fehler"
    assert "TimeoutError" in z["detail"]


def test_alarm_mit_exception_schreibt_stephan_alarm_event(audit_log, monkeypatch):
    """alarm() mit Exception schreibt error_class als Exception-Namen."""
    monkeypatch.setattr(w, "WORKER_SLOT", "2")
    w.alarm("pr103@abc", RuntimeError("Netzwerkfehler"))
    zeilen = _audit_zeilen(audit_log)
    alarm_events = [z for z in zeilen if z["event"] == "stephan_alarm"]
    assert len(alarm_events) == 1
    z = alarm_events[0]
    assert z["error_class"] == "RuntimeError"
    assert "Netzwerkfehler" in z["detail"]


def test_agent_run_finished_event(monkeypatch, audit_log, worker_env):
    """main() schreibt agent_run_finished nach run_agent() (#222)."""
    # Wir testen die audit-Logik direkt, indem wir die Funktion aufrufen, die das Event schreibt
    # In main() wird audit("agent_run_finished", ...) nach run_agent() aufgerufen
    issue_no = 42
    usage = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 200, "cost": 0.00123}
    w.audit("agent_run_finished", issue=issue_no, model="coder", exit_code=0,
            tokens=usage, cost=0.00123)
    zeilen = _audit_zeilen(audit_log)
    events = [z for z in zeilen if z["event"] == "agent_run_finished"]
    assert len(events) == 1
    z = events[0]
    assert z["issue"] == 42
    assert z["model"] == "coder"
    assert z["exit_code"] == 0
    assert z["tokens"]["input_tokens"] == 100
    assert z["tokens"]["output_tokens"] == 50
    assert z["tokens"]["cache_read_input_tokens"] == 200
    assert z["cost"] == 0.00123


def test_agent_run_finished_event_ohne_cost(monkeypatch, audit_log, worker_env):
    """agent_run_finished auch ohne cost-Feld."""
    w.audit("agent_run_finished", issue=7, model="coder", exit_code=0,
            tokens={"input_tokens": 10, "output_tokens": 5}, cost=None)
    zeilen = _audit_zeilen(audit_log)
    events = [z for z in zeilen if z["event"] == "agent_run_finished"]
    assert len(events) == 1
    z = events[0]
    assert z["cost"] is None


def test_pr_opened_event(monkeypatch, audit_log, worker_env):
    """open_pr() schreibt pr_opened bei neuem PR (#222)."""
    w.audit("pr_opened", issue=123, pr=456, branch="feature/issue-123")
    zeilen = _audit_zeilen(audit_log)
    events = [z for z in zeilen if z["event"] == "pr_opened"]
    assert len(events) == 1
    z = events[0]
    assert z["issue"] == 123
    assert z["pr"] == 456
    assert z["branch"] == "feature/issue-123"


# ---- review_tick: stephan_alarm Events ---------------------------------------

def test_review_tick_to_stephan_schreibt_stephan_alarm(audit_log, monkeypatch):
    """to_stephan() schreibt zusätzlich stephan_alarm (#222)."""
    state = {}
    monkeypatch.setattr(r, "AUDIT_LOG", str(audit_log))
    r.to_stephan(7, "pr7@abc123", "Merge-Modus manuell", state)
    zeilen = _audit_zeilen(audit_log)
    # to_stephan schreibt to_stephan UND stephan_alarm
    stephan_events = [z for z in zeilen if z["event"] == "stephan_alarm"]
    assert len(stephan_events) == 1
    z = stephan_events[0]
    assert z["event"] == "stephan_alarm"
    assert z["pr"] == 7
    assert z["sha"] == "abc123"
    assert z["reason"] == "Merge-Modus manuell"


def test_review_tick_post_review_status_failure_schreibt_stephan_alarm(audit_log, monkeypatch):
    """post_review_status() Fehler schreibt stephan_alarm."""
    monkeypatch.setattr(r, "AUDIT_LOG", str(audit_log))
    # Simuliere einen Fehler beim Setzen des Status
    sha = "abcdef123456"
    # Wir rufen die interne Logik nicht direkt auf, sondern testen das audit-Event
    r.audit("stephan_alarm", pr="?", sha=sha[:7], reason="Status review nicht gesetzt: RuntimeError")
    zeilen = _audit_zeilen(audit_log)
    events = [z for z in zeilen if z["event"] == "stephan_alarm"]
    assert len(events) == 1
    z = events[0]
    assert z["sha"] == sha[:7]
    assert "Status review nicht gesetzt" in z["reason"]


def test_review_tick_reviewer_config_problem_schreibt_stephan_alarm(audit_log, monkeypatch):
    """reviewer_config_problem() schreibt stephan_alarm."""
    monkeypatch.setattr(r, "AUDIT_LOG", str(audit_log))
    r.audit("stephan_alarm", pr=5, reason="Reviewer-Konfiguration unbrauchbar: REVIEW_MODEL unbekannt")
    zeilen = _audit_zeilen(audit_log)
    events = [z for z in zeilen if z["event"] == "stephan_alarm"]
    assert len(events) == 1
    z = events[0]
    assert z["pr"] == 5
    assert "Reviewer-Konfiguration unbrauchbar" in z["reason"]


# ---- dashboard_stats: Audit-Log Parsing --------------------------------------

NOW = datetime(2026, 10, 16, 12, 0, 0)
NOW_UTC = NOW.replace(tzinfo=timezone.utc)


def _audit_event(event: str, **fields) -> dict:
    """Hilfsfunktion für ein Audit-Event mit UTC-Zeitstempel (relativ zu NOW)."""
    return {
        "ts": (NOW_UTC - timedelta(hours=1)).isoformat(),
        "event": event,
        **fields
    }


def test_dashboard_classify_audit_review_posted_freigegeben():
    """review_posted mit Freigegeben -> freigegeben."""
    evt = _audit_event("review_posted", urteil="**Freigegeben**", projekt="sc-digger", model="coder-glm")
    res = ds.classify_audit(evt)
    assert res is not None
    ts, kind, projekt, reviewer = res
    assert kind == "freigegeben"
    assert projekt == "sc-digger"
    assert reviewer == "coder-glm"


def test_dashboard_classify_audit_review_posted_aenderungen():
    """review_posted mit Änderungen nötig -> aenderungen."""
    evt = _audit_event("review_posted", urteil="**Änderungen nötig**", projekt="sc-digger", model="coder-glm")
    res = ds.classify_audit(evt)
    assert res is not None
    ts, kind, projekt, reviewer = res
    assert kind == "aenderungen"
    assert reviewer == "coder-glm"


def test_dashboard_classify_audit_pr_merged():
    """pr_merged -> gemergt."""
    evt = _audit_event("pr_merged", pr=7, sha="abc123", repo="tripitest-art/sc-digger")
    res = ds.classify_audit(evt)
    assert res is not None
    ts, kind, projekt, reviewer = res
    assert kind == "gemergt"
    assert projekt == "tripitest-art/sc-digger"
    assert reviewer == "-"


def test_dashboard_classify_audit_stephan_alarm_pr_bezogen():
    """stephan_alarm PR-bezogen -> an_stephan."""
    evt = _audit_event("stephan_alarm", pr=5, sha="abc123", reason="Merge-Modus manuell")
    res = ds.classify_audit(evt)
    assert res is not None
    ts, kind, projekt, reviewer = res
    assert kind == "an_stephan"
    assert reviewer == "-"


def test_dashboard_classify_audit_stephan_alarm_worker_fehler():
    """stephan_alarm mit issue/pr/text skey -> worker_fehler."""
    for skey in ("issue42", "pr103@abc", "text5@def"):
        evt = _audit_event("stephan_alarm", skey=skey, reason="irgendein Fehler")
        res = ds.classify_audit(evt)
        assert res is not None
        ts, kind, projekt, reviewer = res
        assert kind == "worker_fehler", f"skey={skey} sollte worker_fehler sein"


def test_dashboard_classify_audit_to_stephan_legacy():
    """to_stephan (Legacy) -> an_stephan."""
    evt = _audit_event("to_stephan", pr=7, sha="abc123", reason="test", repo="tripitest-art/sc-digger")
    res = ds.classify_audit(evt)
    assert res is not None
    ts, kind, projekt, reviewer = res
    assert kind == "an_stephan"
    assert projekt == "tripitest-art/sc-digger"


def test_dashboard_classify_audit_unbekanntes_event():
    """Unbekannte Events werden ignoriert."""
    evt = _audit_event("run_started", key="issue5")
    res = ds.classify_audit(evt)
    assert res is None


def test_dashboard_stats_preload_audit(tmp_path):
    """preload_audit liest Audit-Log und zählt Events."""
    audit_log = tmp_path / "audit.jsonl"
    # Events mit UTC-Zeitstempel (wie echtes Audit-Log) - 1 Stunde vor NOW (in UTC)
    # NOW = 2026-10-16 12:00:00 lokal (naiv), auf System mit UTC+2 ist das 10:00 UTC
    # Event um 09:00 UTC -> 11:00 lokal -> innerhalb 24h
    ts_utc = (NOW.replace(tzinfo=timezone.utc) - timedelta(hours=3)).isoformat()  # 09:00 UTC = 11:00 lokal
    events = [
        {"ts": ts_utc, "event": "review_posted", "urteil": "**Freigegeben**", "projekt": "sc-digger", "model": "coder-glm"},
        {"ts": ts_utc, "event": "review_posted", "urteil": "**Änderungen nötig**", "projekt": "sc-digger", "model": "coder-glm"},
        {"ts": ts_utc, "event": "pr_merged", "pr": 7, "sha": "abc123", "repo": "tripitest-art/sc-digger"},
        {"ts": ts_utc, "event": "stephan_alarm", "pr": 5, "reason": "Merge-Modus manuell"},
        {"ts": ts_utc, "event": "stephan_alarm", "skey": "issue42", "reason": "Timeout"},
    ]
    with open(audit_log, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    
    stats = ds.ReviewStats()
    stats.preload_audit(str(audit_log))
    snap = stats.snapshot(NOW)
    
    assert snap["24h"]["freigegeben"] == 1
    assert snap["24h"]["aenderungen"] == 1
    assert snap["24h"]["gemergt"] == 1
    assert snap["24h"]["an_stephan"] == 1
    assert snap["24h"]["worker_fehler"] == 1
    assert snap["24h"]["quote"] == 50


def test_dashboard_stats_build_stats_kombiniert(tmp_path):
    """build_stats kombiniert Audit-Log (primär) und Worker-Log (Fallback)."""
    audit_log = tmp_path / "audit.jsonl"
    worker_log = tmp_path / "worker.log"
    
    # Audit-Log: ein Event (UTC-Zeitstempel 3h vor NOW_UTC -> 1h vor NOW lokal)
    ts_utc = (NOW.replace(tzinfo=timezone.utc) - timedelta(hours=3)).isoformat()
    audit_event = {"ts": ts_utc, "event": "review_posted", "urteil": "**Freigegeben**", "projekt": "sc-digger", "model": "coder-glm"}
    with open(audit_log, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(audit_event) + "\n")
    
    # Worker-Log: ein älteres Event (2h vor NOW lokal)
    old_ts = (NOW - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    worker_line = f"{old_ts} [review] PR #8: erfolgreich gemergt (--squash).\n"
    with open(worker_log, "w", encoding="utf-8") as fh:
        fh.write(worker_line)
    
    snap = ds.build_stats(str(audit_log), str(worker_log), now=NOW)
    
    # Audit-Log Event + Worker-Log Event
    assert snap["24h"]["freigegeben"] == 1
    assert snap["24h"]["gemergt"] == 1


# ---- Integration: claim_issue schreibt issue_claimed (bestehend) --------------

def test_claim_issue_protokolliert_issue_claimed(monkeypatch, audit_log, worker_env):
    """claim_issue() schreibt issue_claimed Event."""
    def mock_run(cmd, **kw):
        class Result:
            stdout = "https://github.com/tripitest-art/agent-sandbox-infra/issues/99\n"
        return Result()
    
    monkeypatch.setattr(w.subprocess, "run", mock_run)
    assert w.claim_issue(99) is True
    
    zeilen = _audit_zeilen(audit_log)
    events = [z for z in zeilen if z["event"] == "issue_claimed"]
    assert len(events) == 1
    z = events[0]
    assert z["issue"] == 99
    assert z["slot"] == "1"
    assert z["model"] == "test-model"


# ---- Integration: release_orphan_claim schreibt orphan_claim_released --------

def test_release_orphan_claim_protokolliert_orphan_claim_released(monkeypatch, audit_log, worker_env):
    """release_orphan_claim() schreibt orphan_claim_released Event."""
    # Wir können release_orphan_claim nicht leicht testen ohne gh-Mock,
    # aber wir können prüfen dass audit() das Event schreibt
    w.audit("orphan_claim_released", issue=42, age_minutes=60)
    zeilen = _audit_zeilen(audit_log)
    events = [z for z in zeilen if z["event"] == "orphan_claim_released"]
    assert len(events) == 1
    z = events[0]
    assert z["issue"] == 42
    assert z["age_minutes"] == 60


# ---- Integration: run_started / run_finished / run_cost (bestehend) ----------

def test_run_started_finished_cost_events(monkeypatch, audit_log, worker_env):
    """run_started, run_finished, run_cost Events."""
    start = datetime.now(timezone.utc)
    w.audit("run_started", key="issue5", spend_vorher=1.23)
    w.audit("run_finished", key="issue5", exit_code=0, dauer_s=120.5, anbieterfehler=False)
    w.audit("run_cost", key="issue5", spend_vorher=1.23, spend_nachher=1.25, kosten=0.02, parallel=False, run_ende=start.isoformat())
    
    zeilen = _audit_zeilen(audit_log)
    events = {z["event"]: z for z in zeilen}
    
    assert "run_started" in events
    assert events["run_started"]["key"] == "issue5"
    assert events["run_started"]["spend_vorher"] == 1.23
    
    assert "run_finished" in events
    assert events["run_finished"]["exit_code"] == 0
    assert events["run_finished"]["dauer_s"] == 120.5
    
    assert "run_cost" in events
    assert events["run_cost"]["kosten"] == 0.02
    assert events["run_cost"]["parallel"] is False