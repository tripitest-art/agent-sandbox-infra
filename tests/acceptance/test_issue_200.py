import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)


def test_limits_vorhanden():
    assert getattr(w, "PR_LIST_LIMIT", None) == 10
    assert getattr(w, "ISSUE_LIST_LIMIT", None) == 20


def test_rework_job_nutzt_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(w, "gh", lambda *args: seen.append(args) or [])
    w.rework_job(frozenset(), lambda k: False)
    assert any(c[:2] == ("pr", "list") and "--limit" in c and c[c.index("--limit") + 1] == "10" for c in seen)


def test_text_fix_job_nutzt_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(w, "gh", lambda *args: seen.append(args) or [])
    w.text_fix_job(lambda k: False)
    assert any(c[:2] == ("pr", "list") and "--limit" in c and c[c.index("--limit") + 1] == "10" for c in seen)


def test_active_in_progress_issues_nutzt_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(w, "gh", lambda *args: seen.append(args) or [])
    w.active_in_progress_issues()
    assert any(c[:2] == ("issue", "list") and "--limit" in c and c[c.index("--limit") + 1] == "20" for c in seen)


def test_issue_job_nutzt_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(w, "gh", lambda *args: seen.append(args) or [])
    monkeypatch.setattr(w, "active_in_progress_issues", lambda: [])
    w.issue_job(lambda k: False)
    assert any(c[:2] == ("issue", "list") and "--limit" in c and c[c.index("--limit") + 1] == "20" for c in seen)
