import importlib.util
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_issue_273", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_issue_273"] = r
_spec.loader.exec_module(r)

T0 = datetime(2026, 10, 22, 17, 31, tzinfo=timezone.utc)
MARKE = "⚠ Stephan: Reviewer-Modell"
SHA = "a" * 40
GOOD_CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"} for n in ("tests", "acceptance-guard", "CodeQL")]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    p = r.projects.default_projects()[0]
    monkeypatch.setattr(r, "PROJECTS", [p])
    monkeypatch.setattr(r, "REPO", p.repo)
    monkeypatch.setattr(r, "REVIEW_SKILL", p.review_skill)
    monkeypatch.setattr(r, "PROJECT_AUTO_MERGE", p.auto_merge)
    monkeypatch.setattr(r, "PROJECT_MANUAL_PATHS", p.manual_paths)
    monkeypatch.setattr(r, "BASE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "BASE_DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "REVIEW_KEY", "test-key")
    monkeypatch.setattr(r, "REVIEWER_NAME", "GLM")
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "coder-glm")
    monkeypatch.setattr(r, "POST", False)
    monkeypatch.setattr(r, "AUTO_MERGE", True)
    monkeypatch.setattr(r.worker_tick, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))

    def kein_netz(*a, **k):
        raise urllib.error.URLError("kein Netz im Test")
    monkeypatch.setattr(r.urllib.request, "urlopen", kein_netz)


def _meldungen(capsys):
    return [z for z in capsys.readouterr().out.splitlines() if MARKE in z]


def test_vorgaben():
    assert r.REVIEW_OUTAGE_FAILS == 3
    assert r.REVIEW_OUTAGE_MINUTEN == 30


def test_meldung_erst_ab_dem_dritten_fehlschlag_und_nur_einmal(capsys):
    state = {}
    exc = urllib.error.URLError("geheimer Text")
    r.note_llm_outage(state, "coder-glm", exc, 272, now=T0)
    r.note_llm_outage(state, "coder-glm", exc, 272, now=T0 + timedelta(minutes=3))
    assert _meldungen(capsys) == []
    r.note_llm_outage(state, "coder-glm", exc, 272, now=T0 + timedelta(minutes=6))
    zeilen = _meldungen(capsys)
    assert len(zeilen) == 1
    z = zeilen[0]
    assert "coder-glm" in z and "PR #272" in z and "URLError" in z and "6 Minuten" in z
    assert "geheimer Text" not in z
    r.note_llm_outage(state, "coder-glm", exc, 272, now=T0 + timedelta(minutes=9))
    assert _meldungen(capsys) == []
    assert state["ausfall"]["coder-glm"]["gemeldet"] is True
    assert state["ausfall"]["coder-glm"]["fehler"] == 4


def test_meldung_auch_nach_dreissig_minuten_mit_wenigen_fehlschlaegen(capsys):
    state = {}
    exc = OSError("x")
    r.note_llm_outage(state, "coder-glm", exc, 5, now=T0)
    assert _meldungen(capsys) == []
    r.note_llm_outage(state, "coder-glm", exc, 5, now=T0 + timedelta(minutes=31))
    zeilen = _meldungen(capsys)
    assert len(zeilen) == 1 and "31 Minuten" in zeilen[0] and "OSError" in zeilen[0]


def test_erfolg_beendet_die_serie_und_ein_neuer_ausfall_meldet_wieder(capsys):
    state = {}
    exc = urllib.error.URLError("x")
    for minute in (0, 1, 2):
        r.note_llm_outage(state, "coder-glm", exc, 7, now=T0 + timedelta(minutes=minute))
    assert len(_meldungen(capsys)) == 1
    r.note_llm_ok(state, "coder-glm")
    assert "coder-glm" not in state.get("ausfall", {})
    for minute in (60, 61):
        r.note_llm_outage(state, "coder-glm", exc, 7, now=T0 + timedelta(minutes=minute))
    assert _meldungen(capsys) == []
    r.note_llm_outage(state, "coder-glm", exc, 7, now=T0 + timedelta(minutes=62))
    assert len(_meldungen(capsys)) == 1


def test_erfolg_dazwischen_setzt_den_zaehler_zurueck(capsys):
    state = {}
    exc = urllib.error.URLError("x")
    r.note_llm_outage(state, "coder-glm", exc, 7, now=T0)
    r.note_llm_outage(state, "coder-glm", exc, 7, now=T0 + timedelta(minutes=1))
    r.note_llm_ok(state, "coder-glm")
    r.note_llm_outage(state, "coder-glm", exc, 7, now=T0 + timedelta(minutes=2))
    r.note_llm_outage(state, "coder-glm", exc, 7, now=T0 + timedelta(minutes=3))
    assert _meldungen(capsys) == []


def test_zaehler_sind_je_modellalias_getrennt(capsys):
    state = {}
    exc = urllib.error.URLError("x")
    for minute in (0, 1):
        r.note_llm_outage(state, "coder-glm", exc, 7, now=T0 + timedelta(minutes=minute))
        r.note_llm_outage(state, "longctx", exc, 7, now=T0 + timedelta(minutes=minute))
    assert _meldungen(capsys) == []
    r.note_llm_outage(state, "coder-glm", exc, 7, now=T0 + timedelta(minutes=2))
    zeilen = _meldungen(capsys)
    assert len(zeilen) == 1 and "coder-glm" in zeilen[0]


def test_zustand_wird_gespeichert(tmp_path):
    state = {}
    r.note_llm_outage(state, "coder-glm", urllib.error.URLError("x"), 7, now=T0)
    assert r.load_state()["ausfall"]["coder-glm"]["fehler"] == 1


def test_review_takt_meldet_ausfall_einmal_ueber_mehrere_takte(monkeypatch, capsys):
    pr = {"number": 7, "title": "Titel", "body": "Closes #5\n\nWorker: DeepSeek (LiteLLM coder-fast)\n",
          "headRefName": "feature/issue-5", "headRefOid": SHA, "isCrossRepository": False, "reviews": [],
          "commits": [{"committedDate": "2026-10-02T10:00:00Z"}], "statusCheckRollup": GOOD_CHECKS}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "Das Issue", "body": "Betroffene Dateien: a.py"}
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 3, "deletions": 0}], "state": "OPEN", "headRefOid": SHA}
        raise AssertionError(args)

    def call_llm(model, messages):
        raise urllib.error.URLError("Modell weg")

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/a.py b/a.py\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx" if "AGENTS" in p else "## Schritt 5\nf\n## Schritt 6\nd\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", call_llm)
    for _ in range(2):
        assert r.run_once() == 0
    assert _meldungen(capsys) == []
    assert r.run_once() == 0
    zeilen = _meldungen(capsys)
    assert len(zeilen) == 1 and "coder-glm" in zeilen[0] and "PR #7" in zeilen[0]
    assert r.run_once() == 0
    assert _meldungen(capsys) == []
