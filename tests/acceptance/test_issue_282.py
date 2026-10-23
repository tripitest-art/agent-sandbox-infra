import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_PATH = ROOT / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_issue_264", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_issue_264"] = w
_spec.loader.exec_module(w)

ISSUE_BODY = ("### Ziel\n\nEtwas Besonderes.\n\n### Betroffene Dateien\n\n- `sandbox/a.py`\n\n"
              "### Fertig, wenn\n\n- [ ] Tests grün\n")


class FakeGH:
    """gh-Ersatz: Kandidaten (mit Autor und Dateiliste) und eine leere `in-arbeit`-Liste."""

    def __init__(self, issues):
        self.issues = list(issues)

    def __call__(self, *args, **kwargs):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else self.issues
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Test Issue"}
        if args[:2] in (("issue", "edit"), ("pr", "list"), ("run", "list")) or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _kandidat(nummer=42):
    return {"number": nummer, "title": "Test Issue", "body": ISSUE_BODY,
            "author": {"login": w.OWNER}, "labels": [{"name": "bereit"}]}


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    p = w.projects.default_projects()[0]
    monkeypatch.setattr(w, "PROJECTS", [p])
    monkeypatch.setattr(w, "REPO", p.repo)
    monkeypatch.setattr(w, "PROJECT", p.name)
    monkeypatch.setattr(w, "PREFIX", "")
    monkeypatch.setattr(w, "HOTSPOT", p.hotspot)
    monkeypatch.setattr(w, "HOTSPOT_LABEL", p.hotspot_label)
    monkeypatch.setattr(w, "WORKDIR", str(ROOT))
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "WORKER", "TestWorker")
    monkeypatch.setattr(w, "TEST_CMD", "pytest -q")
    monkeypatch.setattr(w, "verwaiste_melden", lambda state, now=None: [])


def _auftrag(monkeypatch):
    monkeypatch.setattr(w, "gh", FakeGH([_kandidat()]))
    job = w.issue_job()
    assert job is not None, "issue_job() fand den Kandidaten nicht"
    key, task, _, _ = job
    assert key == "issue42"
    return task


def test_auftrag_hat_englische_schritte(monkeypatch):
    task = _auftrag(monkeypatch)
    assert "contract" in task.lower()
    assert "tests/acceptance/test_issue_42.py" in task
    assert "pytest -q" in task
    assert "gh issue edit 42 --add-label in-arbeit --remove-label bereit" in task
    for deutsch in ("Gehe genau diese Schritte durch", "Das Issue steht unten", "Hältst du einen Akzeptanztest"):
        assert deutsch not in task


def test_kennungszeile_und_issue_text_bleiben_erhalten(monkeypatch):
    task = _auftrag(monkeypatch)
    assert task.startswith("Du setzt Issue #42 um")
    assert "Worker: TestWorker" in task
    assert ISSUE_BODY in task
    assert "----- Issue #42: Test Issue -----" in task


def test_push_regel_ist_englisch_und_nennt_die_ausnahme():
    regel = w.PUSH_RULE
    assert "pre-push" in regel.lower()
    assert "--no-verify" in regel
    assert "Der Push wird bei roten Tests abgelehnt" not in regel
    assert any(wort in regel.lower() for wort in ("rejected", "fails", "failing"))


def test_fehlerregel_ist_englisch_und_behaelt_den_platzhalter():
    text = w.ERROR_RULE.format(where="in issue #7")
    assert "in issue #7" in text
    assert "Gibt ein Befehl eine Fehlermeldung aus" not in text
    assert "comment" in text.lower()
    assert "NOT" in text


def test_pr_regeln_sind_englisch_und_enthalten_die_vorlage():
    regeln = w.template_rules()
    assert "Regeln für den PR-Text" not in regeln
    assert "heading" in regeln.lower()
    assert w.pr_template().strip() in regeln
