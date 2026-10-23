import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_PATH = ROOT / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_issue_N", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_issue_N"] = w
_spec.loader.exec_module(w)

ISSUE_BODY = ("### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `sandbox/a.py`\n\n"
              "### Fertig, wenn\n\n- [ ] Tests gruen\n")


class FakeGH:
    """gh-Ersatz: Kandidaten- und `in-arbeit`-Liste getrennt; der Kandidat hat Autor und
    eine Betroffene-Dateien-Liste (Regel 8)."""

    def __init__(self, issues):
        self.issues = list(issues)
        self.claims = []

    def __call__(self, *args, **kwargs):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else self.issues
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Test Issue"}
        if args[:2] == ("issue", "edit"):
            self.claims.append(int(args[2]))
            return None
        if args[:2] in (("pr", "list"), ("run", "list")) or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _kandidat(nummer=42):
    return {"number": nummer, "title": "Test Issue", "body": ISSUE_BODY,
            "author": {"login": w.OWNER}, "labels": [{"name": "bereit"}]}


def test_issue_job_schritt_1_ist_feststellung_ohne_pr_vorlage(monkeypatch):
    monkeypatch.setattr(w, "gh", FakeGH([_kandidat()]))
    monkeypatch.setattr(w, "pr_template", lambda: "MARKER-PR-VORLAGE-7f3a2c")
    job = w.issue_job()
    assert job is not None, "issue_job() fand den Kandidaten nicht"
    key, task, pr_no, sha = job
    assert key == "issue42" and pr_no is None and sha is None
    assert "MARKER-PR-VORLAGE-7f3a2c" not in task
    assert "gh issue edit 42 --add-label in-arbeit --remove-label bereit" in task
    assert "1. `gh issue edit" not in task
    assert w.PUSH_RULE in task


def test_main_test_pflicht_gilt_auch_ohne_push(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "gh", FakeGH([_kandidat(7)]))
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "choose_model", lambda: ("coder", []))
    gelaufen = []
    monkeypatch.setattr(w, "run_agent", lambda prompt: gelaufen.append(prompt) or 0)
    assert w.main() == 0
    assert len(gelaufen) == 1
    assert "vor jedem Push" not in gelaufen[0]
    assert w.TEST_CMD in gelaufen[0]
