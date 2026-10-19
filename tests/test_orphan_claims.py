"""Tests für die Erkennung und Freigabe verwaister in-arbeit-Claims und Dauerkollisions-Meldungen."""

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

T0 = 1760880000.0  # Feste Epochen-Sekunden für reproduzierbare Tests
MIN = 60.0


def iso(sekunden: float) -> str:
    return datetime.fromtimestamp(sekunden, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    monkeypatch.setattr(w, "PREFIX", "")
    p = w.projects.default_projects()[0]
    monkeypatch.setattr(w, "PROJECTS", [p])
    monkeypatch.setattr(w, "REPO", p.repo)
    monkeypatch.setattr(w, "PROJECT", p.name)
    monkeypatch.setattr(w, "BASE_WORKDIR", str(tmp_path / "workdir"))
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path / "workdir"))
    if hasattr(w, "reset_kollision_merker"):
        w.reset_kollision_merker()


class MockGH:
    def __init__(self, active=(), issues=(), prs=(), branches=(), events=None,
                 fail_events=False, fail_branches=False, fail_prs=False):
        self.active = list(active)
        self.issues = list(issues)
        self.prs = list(prs)
        self.branches = list(branches)
        self.events = dict(events or {})
        self.fail_events = fail_events
        self.fail_branches = fail_branches
        self.fail_prs = fail_prs
        self.edits: list[tuple] = []
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            if self.fail_prs:
                raise RuntimeError("gh pr list fehlgeschlagen")
            return self.prs
        if args[:2] == ("issue", "list"):
            if "in-arbeit" in args:
                return self.active
            return self.issues
        if args[:2] == ("issue", "edit"):
            self.edits.append(args)
            return "https://github.com/.../issues/..."
        if args and args[0] == "api":
            endpoint = args[1] if len(args) > 1 else ""
            if "branches" in endpoint:
                if self.fail_branches:
                    raise RuntimeError("gh api branches fehlgeschlagen")
                return [{"name": b} if isinstance(b, str) else b for b in self.branches]
            if "/events" in endpoint:
                if self.fail_events:
                    raise RuntimeError("gh api events fehlgeschlagen")
                # endpoint: repos/<REPO>/issues/<N>/events
                import re
                m = re.search(r"issues/(\d+)/events", endpoint)
                if m:
                    n = int(m.group(1))
                    return self.events.get(n, [])
                return []
            return []
        if args[:2] == ("run", "list"):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _issue_dict(nummer: int, alter_minuten: float, dateien=("sandbox/worker_tick.py",),
                author="tripitest-art", labels=("in-arbeit",)):
    body = "### Betroffene Dateien\n\n" + "".join(f"- `{d}`\n" for d in dateien)
    return {
        "number": nummer,
        "title": f"Issue {nummer}",
        "body": body,
        "author": {"login": author},
        "labels": [{"name": lab} for lab in labels],
        "updatedAt": iso(T0 - alter_minuten * MIN),
    }


def _events_with_labeled(alter_minuten: float, label_name="in-arbeit"):
    return [
        {
            "id": 1,
            "event": "labeled",
            "created_at": iso(T0 - alter_minuten * MIN),
            "label": {"name": label_name},
        }
    ]


def test_verwaister_claim_wird_freigegeben_und_kollision_verschwindet(monkeypatch, capsys):
    """Verwaister Claim (50 min alt, kein Branch, kein PR, kein Slot) -> Label zurück, alarm, Kollision weg, Kandidat gewählt."""
    aktives_issue = _issue_dict(324, alter_minuten=50)
    kandidat = _issue_dict(364, alter_minuten=10, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        prs=[],
        branches=["main"],
        events={324: _events_with_labeled(50)},
    )
    monkeypatch.setattr(w, "gh", mock)

    key, task, pr_no, before = w.issue_job(now=T0)

    # 1. Kandidat 364 wurde gewählt, da 324 freigegeben wurde und nicht mehr kollidiert
    assert key == "issue364"

    # 2. Label von 324 wurde zurückgesetzt
    assert any(call[:3] == ("issue", "edit", "324") and "--add-label" in call and "bereit" in call
               and "--remove-label" in call and "in-arbeit" in call for call in mock.edits)

    # 3. Alarm-Zeile geschrieben
    out = capsys.readouterr().out
    assert "⚠ Stephan:" in out and "324" in out and "50" in out

    # 4. Audit-Log hat orphan_claim_released
    audit_zeilen = [json.loads(z) for z in Path(w.AUDIT_LOG).read_text(encoding="utf-8").splitlines() if z.strip()]
    rel = [z for z in audit_zeilen if z.get("event") == "orphan_claim_released"]
    assert len(rel) == 1 and rel[0]["issue"] == 324


def test_claim_juenger_als_orphan_minuten_bleibt_aktiv(monkeypatch, capsys):
    """Claim jünger als ORPHAN_MINUTEN (20 min < 45 min) -> unverändert aktiv, Kollision bleibt bestehen."""
    aktives_issue = _issue_dict(324, alter_minuten=20)
    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        prs=[],
        branches=["main"],
        events={324: _events_with_labeled(20)},
    )
    monkeypatch.setattr(w, "gh", mock)

    job = w.issue_job(now=T0)
    assert job is None  # Kollision mit aktiver 324
    assert mock.edits == []  # Nichts editiert
    out = capsys.readouterr().out
    assert "⚠ Stephan:" not in out
    assert "Datei-Kollision mit aktivem Auftrag" in out


def test_branch_auf_github_zaehlt_nicht_und_verhindert_freigabe_nicht(monkeypatch, capsys):
    """Ein Branch auf GitHub zählt nicht: der Takt pusht erst nach dem Lauf.
    Ein Issue mit Branch auf GitHub, aber ohne PR/Slot und älter als ORPHAN_MINUTEN wird freigegeben."""
    aktives_issue = _issue_dict(324, alter_minuten=50)
    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        prs=[],
        branches=["main", "feature/issue-324-fix"],
        events={324: _events_with_labeled(50)},
    )
    monkeypatch.setattr(w, "gh", mock)

    key, task, pr_no, before = w.issue_job(now=T0)
    assert key == "issue364"
    assert any(call[:3] == ("issue", "edit", "324") for call in mock.edits)
    out = capsys.readouterr().out
    assert "⚠ Stephan:" in out and "324" in out


def test_claim_mit_offenem_pr_bleibt_aktiv(monkeypatch, capsys):
    """Claim mit offenem PR auf issue_branch(324) -> nie freigegeben."""
    aktives_issue = _issue_dict(324, alter_minuten=50)
    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        prs=[{"number": 99, "headRefName": "feature/issue-324"}],
        branches=["main"],
        events={324: _events_with_labeled(50)},
    )
    monkeypatch.setattr(w, "gh", mock)

    job = w.issue_job(now=T0)
    assert job is None
    assert mock.edits == []
    out = capsys.readouterr().out
    assert "⚠ Stephan:" not in out


def test_claim_mit_aktivem_slot_bleibt_aktiv(monkeypatch, capsys):
    """Claim mit lebender Slot-Belegung in CLAIM_DIR -> nie freigegeben."""
    aktives_issue = _issue_dict(324, alter_minuten=50)
    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        prs=[],
        branches=["main"],
        events={324: _events_with_labeled(50)},
    )
    monkeypatch.setattr(w, "gh", mock)

    # Belegung durch aktuellen Prozess anlegen
    cpath = w.claim_path("issue324")
    os.makedirs(os.path.dirname(cpath), exist_ok=True)
    with open(cpath, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))

    job = w.issue_job(now=T0)
    assert job is None
    assert mock.edits == []
    out = capsys.readouterr().out
    assert "⚠ Stephan:" not in out


def test_abfrage_alter_scheitert_nichts_freigegeben(monkeypatch, capsys):
    """GitHub-Abfrage für Alter scheitert (kein updatedAt, Events werfen) -> nichts freigegeben, Warnung im Log."""
    aktives_issue = _issue_dict(324, alter_minuten=50)
    del aktives_issue["updatedAt"]
    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        prs=[],
        branches=["main"],
        fail_events=True,
    )
    monkeypatch.setattr(w, "gh", mock)

    job = w.issue_job(now=T0)
    assert job is None
    assert mock.edits == []
    out = capsys.readouterr().out
    assert "Warnung:" in out and "324" in out
    assert "⚠ Stephan:" not in out


def test_alter_aus_updated_at_genuegt_ohne_extra_events_abfrage(monkeypatch, capsys):
    """updatedAt aus GitHub (gh issue list) bestimmt das Alter direkt ohne zusätzliche Events-API."""
    aktives_issue = _issue_dict(324, alter_minuten=50)
    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        prs=[],
        fail_events=True,  # Even if events endpoint fails, updatedAt from issue is used
    )
    monkeypatch.setattr(w, "gh", mock)

    key, task, pr_no, before = w.issue_job(now=T0)
    assert key == "issue364"
    assert any(call[:3] == ("issue", "edit", "324") for call in mock.edits)
    out = capsys.readouterr().out
    assert "⚠ Stephan:" in out and "324" in out


def test_abfrage_prs_scheitert_nichts_freigegeben(monkeypatch, capsys):
    """GitHub-Abfrage für PRs scheitert -> nichts freigegeben, Warnung im Log."""
    aktives_issue = _issue_dict(324, alter_minuten=50)
    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        fail_prs=True,
        branches=["main"],
        events={324: _events_with_labeled(50)},
    )
    monkeypatch.setattr(w, "gh", mock)

    job = w.issue_job(now=T0)
    assert job is None
    assert mock.edits == []
    out = capsys.readouterr().out
    assert "Warnung:" in out
    assert "⚠ Stephan:" not in out


def test_fremdes_issue_wird_nie_angefasst(monkeypatch, capsys):
    """Fremdes Issue (nicht von OWNER) wird nie freigegeben oder angefasst."""
    fremdes_issue = _issue_dict(324, alter_minuten=50, author="fremder-user")
    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[fremdes_issue],
        issues=[kandidat],
        prs=[],
        branches=["main"],
        events={324: _events_with_labeled(50)},
    )
    monkeypatch.setattr(w, "gh", mock)

    # active_in_progress_issues filtert fremde Issues heraus, sodass fremdes_issue gar nicht erst belegt wird
    # oder falls cleanup_orphan_claims aufgerufen wird, darf fremdes_issue nie editiert werden
    job = w.issue_job(now=T0)
    assert mock.edits == []


def test_dauerkollision_unter_schwelle_kein_alarm_darueber_genau_einmal(monkeypatch, capsys):
    """Dauerkollision: unter Schwelle (60 min) kein Alarm, darüber genau einmal als alarm()."""
    # Aktives Issue 324 hat aktiven Slot, bleibt also dauerhaft aktiv
    aktives_issue = _issue_dict(324, alter_minuten=50)
    cpath = w.claim_path("issue324")
    os.makedirs(os.path.dirname(cpath), exist_ok=True)
    with open(cpath, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))

    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        prs=[],
        branches=["main"],
        events={324: _events_with_labeled(50)},
    )
    monkeypatch.setattr(w, "gh", mock)

    # 1. Takt bei t = T0: Kollision erkannt, aber dauer = 0 -> kein Alarm
    assert w.issue_job(now=T0) is None
    out = capsys.readouterr().out
    assert "⚠ Stephan:" not in out
    assert "Datei-Kollision mit aktivem Auftrag" in out

    # 2. Takt bei t = T0 + 30 min (unter Schwelle 60 min): kein Alarm
    assert w.issue_job(now=T0 + 30 * MIN) is None
    out = capsys.readouterr().out
    assert "⚠ Stephan:" not in out

    # 3. Takt bei t = T0 + 60 min (Schwelle erreicht): genau ein Alarm mit Blocker 324
    assert w.issue_job(now=T0 + 60 * MIN) is None
    out = capsys.readouterr().out
    assert out.count("⚠ Stephan:") == 1
    assert "324" in out
    assert "364" in out

    # 4. Takt bei t = T0 + 70 min: Kollision besteht fort, aber kein weiterer Alarm
    assert w.issue_job(now=T0 + 70 * MIN) is None
    out = capsys.readouterr().out
    assert "⚠ Stephan:" not in out
    assert "Datei-Kollision mit aktivem Auftrag" in out


def test_dauerkollision_unterbrechung_setzt_merker_zurueck(monkeypatch, capsys):
    """Wird die Kollision unterbrochen, wird der Merker zurückgesetzt und läuft neu an."""
    aktives_issue = _issue_dict(324, alter_minuten=50)
    cpath = w.claim_path("issue324")
    os.makedirs(os.path.dirname(cpath), exist_ok=True)
    with open(cpath, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))

    kandidat = _issue_dict(364, alter_minuten=5, labels=("worker-task", "bereit", "agent-qwen"))
    mock = MockGH(
        active=[aktives_issue],
        issues=[kandidat],
        prs=[],
        branches=["main"],
        events={324: _events_with_labeled(50)},
    )
    monkeypatch.setattr(w, "gh", mock)

    # Start und Alarmierung nach 60 min
    w.issue_job(now=T0)
    w.issue_job(now=T0 + 60 * MIN)
    capsys.readouterr()

    # Unterbrechung: kein aktives Issue mehr da
    mock.active = []
    w.issue_job(now=T0 + 70 * MIN)

    # Wieder Kollision bei t = T0 + 80 min: startet frisch
    mock.active = [aktives_issue]
    w.issue_job(now=T0 + 80 * MIN)
    out = capsys.readouterr().out
    assert "⚠ Stephan:" not in out

    # Erst bei t = T0 + 140 min (80 + 60) kommt der nächste Alarm
    w.issue_job(now=T0 + 130 * MIN)
    assert "⚠ Stephan:" not in capsys.readouterr().out
    w.issue_job(now=T0 + 140 * MIN)
    out = capsys.readouterr().out
    assert "⚠ Stephan:" in out and "324" in out
