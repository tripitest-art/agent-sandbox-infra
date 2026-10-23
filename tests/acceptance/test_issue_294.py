import importlib.util
from pathlib import Path
import sys

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

_spec = importlib.util.spec_from_file_location("worker_tick_textfix_test", _SANDBOX / "worker_tick.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_textfix_test"] = w
_spec.loader.exec_module(w)

INCOMPLETE_BODY = "## Was und warum\n\nCloses #\n"
PR_BASE = {"number": 112, "headRefName": "feature/issue-102", "headRefOid": "3373e35abc",
           "isCrossRepository": False, "title": "Implement x"}


def _pr(body):
    return dict(PR_BASE, body=body)


def test_text_fix_job_autofix_erfolg_erzeugt_keinen_auftrag(monkeypatch):
    """Gelingt der deterministische Autofix, bekommt dieser PR keinen Agentenauftrag."""
    edits = []

    def fake_gh(*args):
        if args[:2] == ("pr", "list"):
            return [_pr(INCOMPLETE_BODY)]
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("pr", "edit"):
            edits.append(args)
            return ""
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    monkeypatch.setattr(w, "gh", fake_gh)
    assert w.text_fix_job() is None
    assert len(edits) == 1 and edits[0][2] == "112"


def test_text_fix_job_autofix_scheitert_faellt_auf_agent_zurueck(monkeypatch):
    """Scheitert `gh pr edit`, bleibt der heutige Agentenauftrag als Rückfall."""
    def fake_gh(*args):
        if args[:2] == ("pr", "list"):
            return [_pr(INCOMPLETE_BODY)]
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("pr", "edit"):
            raise RuntimeError("gh nicht erreichbar")
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    monkeypatch.setattr(w, "gh", fake_gh)
    job = w.text_fix_job()
    assert job is not None
    key, task, pr_no, before = job
    assert key == f"text112@{PR_BASE['headRefOid'][:7]}"
    assert pr_no == 112
    assert "„Closes #102“" in task


def test_text_fix_job_erhaelt_vorhandene_worker_zeile(monkeypatch):
    """Eine vorhandene Worker-Zeile wird übernommen statt mit dem WORKER des Takts überschrieben."""
    body = ("## Was und warum\n\nCloses #\n\n"
            "## Umgesetzt von / Review durch\n\nWorker: Gemini Flash\n")
    edits = []

    def fake_gh(*args):
        if args[:2] == ("pr", "list"):
            return [_pr(body)]
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("pr", "edit"):
            edits.append(args)
            return ""
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    monkeypatch.setattr(w, "gh", fake_gh)
    monkeypatch.setattr(w, "WORKER", "DeepSeek (LiteLLM coder)")
    assert w.text_fix_job() is None
    assert len(edits) == 1
    new_body = edits[0][4]
    assert "Worker: Gemini Flash" in new_body
    assert "Worker: DeepSeek (LiteLLM coder)" not in new_body
