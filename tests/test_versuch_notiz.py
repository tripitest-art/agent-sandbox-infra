"""Dedizierte Tests für die Notiz vom Fehlversuch (#346)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_notiz_test", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_notiz_test"] = w
_spec.loader.exec_module(w)

ISSUE_BODY = ("### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `sandbox/a.py`\n\n"
              "### Fertig, wenn\n\n- [ ] Tests gruen\n")

# Ein PR, der die Kriterien von rework_job() erfüllt: OWNER-Review, LABEL am Issue,
# Branch feature/issue-<N>, kein Fork.
PR = {"number": 103, "headRefName": "feature/issue-100-x", "headRefOid": "3373e35",
      "isCrossRepository": False, "commits": [{"committedDate": "2026-09-29T21:37:00Z"}],
      "reviews": [{"submittedAt": "2026-09-29T21:39:23Z",
                   "author": {"login": w.OWNER}, "commit": {"oid": "3373e35"},
                   "body": "**Änderungen nötig**\n\n**Muss**\n\n1. X."}],
      "title": "T", "body": "## Was und warum\n\nCloses #100\n"}


class FakeGH:
    """Fake für gh-Aufrufe in main()."""

    def __init__(self, issues=(), prs=(), kommentar_fehler=None, pr_view=None):
        self.issues = list(issues)
        self.prs = list(prs)
        self.kommentar_fehler = kommentar_fehler
        self.pr_view = pr_view or PR
        self.kommentare = []

    def __call__(self, *args, **kwargs):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else self.issues
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Test Issue"}
        if args[:2] == ("issue", "edit"):
            return None
        if args[:2] == ("issue", "comment"):
            if self.kommentar_fehler:
                raise self.kommentar_fehler
            self.kommentare.append((args[2], args[args.index("--body") + 1]))
            return None
        if args[:2] == ("pr", "list"):
            return self.prs
        if args[:2] == ("pr", "view"):
            return self.pr_view
        if args[:2] == ("run", "list") or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _kandidat():
    return {"number": 7, "title": "Test Issue", "body": ISSUE_BODY,
            "author": {"login": w.OWNER}, "labels": [{"name": "bereit"}]}


@pytest.fixture(autouse=True)
def _isoliert(monkeypatch, tmp_path):
    """Isoliert Tests von Server-Konfiguration und echten Dateien."""
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "WORKER_SLOT", "")
    monkeypatch.setattr(w, "WAKE_PC", False)
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path / "kein-workdir"))
    monkeypatch.setattr(w, "PREFIX", "")
    monkeypatch.setattr(w, "PROJECT", "test")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "ensure_pre_push_hook", lambda: None)
    monkeypatch.setattr(w, "choose_model", lambda *a: ("coder", []))
    monkeypatch.setattr(w, "verwaiste_melden", lambda state, now=None: [])
    monkeypatch.setattr(w, "open_pr", lambda n: False)


def test_kommentarfehler_loggt_nur_und_behaelt_die_notiz(monkeypatch, tmp_path, capsys):
    """Scheitert der Kommentar am Issue, steht das im Log und der Lauf endet trotzdem normal."""
    gh = FakeGH(issues=[_kandidat()], kommentar_fehler=RuntimeError("GraphQL: kaputt"))
    monkeypatch.setattr(w, "gh", gh)

    monkeypatch.setattr(w, "run_agent", lambda prompt: 1)
    assert w.main() == 0
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    notiz = state[w.NOTIZ + "issue7"]
    assert notiz.startswith("Previous attempt failed: agent exited with code 1.")
    assert "issue7: Notiz nicht als Kommentar gepostet (RuntimeError)." in capsys.readouterr().out


def test_nacharbeit_bekommt_keine_notiz(monkeypatch, tmp_path, capsys):
    """Eine Nacharbeit (pr_no is not None, kein n) erhält keine Notiz im Zustand."""
    gh = FakeGH(issues=[])
    monkeypatch.setattr(w, "gh", gh)
    monkeypatch.setattr(w, "verify_rework_pr", lambda prv, oid: (True, ""))
    monkeypatch.setattr(w, "run_agent", lambda prompt: 1)

    # pick_job direkt überspringen: einen Nacharbeits-Auftrag liefern
    before = ("3373e35", "T", "## Was und warum\n\nCloses #100\n")
    monkeypatch.setattr(w, "pick_job", lambda state: ("pr103@2026-09-29T21:39:23Z",
                                                      "Auftrag: PR #103 nachbessern", 103, before))
    assert w.main() == 0
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    # Kein Notiz-Schlüssel für den Nacharbeits-Auftrag
    notiz_keys = [k for k in state if k.startswith(w.NOTIZ)]
    assert notiz_keys == [], f"unerwartete Notiz-Schlüssel: {notiz_keys}"
    # Auch kein Kommentar am Issue (es gibt gar kein n)
    assert gh.kommentare == []


def test_infrastrukturfehler_ohne_notiz(monkeypatch, tmp_path, capsys):
    """Ein Infrastrukturfehler im Lauf (EACCES) bekommt keine Notiz – der Versuch zählt nicht."""
    gh = FakeGH(issues=[_kandidat()])
    monkeypatch.setattr(w, "gh", gh)
    monkeypatch.setattr(w, "open_pr", lambda n: False)

    def run(prompt):
        w.AGENT_INFRA_FEHLER = "EACCES: permission denied"
        return 1

    monkeypatch.setattr(w, "run_agent", run)
    assert w.main() == 0
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    notiz_keys = [k for k in state if k.startswith(w.NOTIZ)]
    assert notiz_keys == [], f"unerwartete Notiz-Schlüssel: {notiz_keys}"
    assert gh.kommentare == []