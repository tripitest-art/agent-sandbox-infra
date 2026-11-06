import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_schliesst", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_schliesst"] = r
_spec.loader.exec_module(r)

SHA = "a" * 40
KOMMENTAR = "Umgesetzt in #7 (Merge durch den Review-Takt)."


@pytest.fixture
def gh(monkeypatch):
    """gh_json/gh_text getrennt aufzeichnen; `zustand` steuert den Issue-Status."""
    seen = {"json": [], "text": [], "zustand": "OPEN", "logs": [], "audit": []}

    def gh_json(*args, **kw):
        seen["json"].append(list(args))
        if args[:2] == ("issue", "view"):
            return {"state": seen["zustand"]}
        if args[:2] == ("pr", "view"):
            return {"state": "OPEN", "headRefOid": SHA, "mergeStateStatus": "CLEAN"}
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: seen["text"].append(list(a)) or "")
    monkeypatch.setattr(r, "log", seen["logs"].append)
    monkeypatch.setattr(r, "audit", lambda event, **f: seen["audit"].append((event, f)))
    monkeypatch.setattr(r, "save_state", lambda state: None)
    return seen


def test_offenes_issue_wird_mit_kommentar_geschlossen(gh):
    assert r.close_merged_issue(7, 5) is True
    assert gh["json"] == [["issue", "view", "5", "--json", "state"]]
    assert gh["text"] == [["issue", "close", "5", "--comment", KOMMENTAR]]
    assert "Issue #5: nach Merge von PR #7 geschlossen." in gh["logs"]
    assert ("issue_closed", {"issue": 5, "pr": 7}) in gh["audit"]


def test_geschlossenes_issue_bleibt_unberuehrt(gh):
    gh["zustand"] = "CLOSED"
    assert r.close_merged_issue(7, 5) is False
    assert gh["text"] == []


def test_ohne_issue_kein_gh_aufruf(gh):
    assert r.close_merged_issue(7, None) is False
    assert gh["json"] == [] and gh["text"] == []


def test_fehler_beim_schliessen_wirft_nie(monkeypatch, gh):
    def kaputt(*a, **k):
        raise subprocess.CalledProcessError(1, ["gh"], output="", stderr="geheim")

    monkeypatch.setattr(r, "gh_text", kaputt)
    assert r.close_merged_issue(7, 5) is False
    assert gh["logs"] == ["Issue #5: Schließen nach Merge von PR #7 fehlgeschlagen (CalledProcessError)."]


def test_merge_or_queue_schliesst_nach_gelungenem_merge(monkeypatch, gh):
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: True)
    assert r.merge_or_queue(7, SHA, {}, issue_no=5) is True
    assert gh["text"] == [["issue", "close", "5", "--comment", KOMMENTAR]]


def test_merge_or_queue_merkt_das_issue_fuer_den_retry(monkeypatch, gh):
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: False)
    state = {}
    assert r.merge_or_queue(7, SHA, state, issue_no=5) is False
    assert state["merge_offen"]["7"] == {"sha": SHA, "versuche": 0, "issue": 5}
    assert gh["text"] == []


def test_merge_or_queue_ohne_issue_wie_bisher(monkeypatch, gh):
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: True)
    assert r.merge_or_queue(7, SHA, {}) is True
    assert gh["json"] == [] and gh["text"] == []


def test_retry_merges_schliesst_nach_spaetem_merge(monkeypatch, gh):
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: True)
    state = {"merge_offen": {"7": {"sha": SHA, "versuche": 0, "issue": 5}}}
    r.retry_merges(state)
    assert state["merge_offen"] == {}
    assert gh["text"] == [["issue", "close", "5", "--comment", KOMMENTAR]]


# ---- Verdrahtung im Review-Ablauf (Muster aus tests/acceptance/test_issue_134.py) ----

OK_REVIEW = "**Freigegeben**\n\n### Muss\nkeine\n\n### Kann\nkeine\n\n### Geprüft\n- alles"
CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": "2026-11-05T13:29:10Z"}
          for n in ("tests", "acceptance-guard")]


def test_review_ablauf_uebergibt_das_worker_issue(monkeypatch, tmp_path):
    p = r.projects.default_projects()[0]
    for name, wert in {"PROJECTS": [p], "REPO": p.repo, "REVIEW_SKILL": p.review_skill,
                       "PROJECT_AUTO_MERGE": True, "PROJECT_MANUAL_PATHS": ("AGENTS.md", ".github/*"),
                       "REQUIRED_CHECKS": ("tests", "acceptance-guard"),
                       "BASE_STATE": str(tmp_path / "state.json"), "BASE_DRAFT_DIR": str(tmp_path / "reviews"),
                       "STATE": str(tmp_path / "state.json"), "DRAFT_DIR": str(tmp_path / "reviews"),
                       "REVIEW_KEY": "test-key", "REVIEWER_NAME": "GLM", "REVIEW_MODEL": "coder-glm",
                       "REVIEW_MODEL_BIG": "coder-glm", "POST": True, "AUTO_MERGE": True}.items():
        monkeypatch.setattr(r, name, wert)

    def no_network(*a, **k):
        raise r.urllib.error.URLError("kein Netz im Test")

    monkeypatch.setattr(r.urllib.request, "urlopen", no_network)
    pr = {"number": 7, "title": "Titel", "body": "Closes #5\n\nWorker: Claude Sonnet 5.5\n",
          "headRefName": "feature/issue-5", "headRefOid": SHA, "isCrossRepository": False, "isDraft": False,
          "labels": [], "reviews": [], "commits": [{"committedDate": "2026-11-05T10:00:00Z"}],
          "statusCheckRollup": CHECKS}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "docs/a.md", "additions": 1, "deletions": 0}]}
        if args[:2] == ("issue", "list"):
            return []
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-claude"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "Issue", "body": "### Merge-Modus\n\nautomatisch nach Review\n"}
        raise AssertionError(args)

    geschlossen = []
    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/docs/a.md b/docs/a.md\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", lambda model, messages: (OK_REVIEW, {"prompt_tokens": 1, "completion_tokens": 1}))
    monkeypatch.setattr(r, "post_review", lambda n, t: None)
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: True)
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: True)
    monkeypatch.setattr(r, "to_stephan", lambda n, key, reason, state: None)
    monkeypatch.setattr(r, "close_merged_issue", lambda pr_no, issue_no: geschlossen.append((pr_no, issue_no)) or True)
    assert r.run_once() == 0
    assert geschlossen == [(7, 5)]
