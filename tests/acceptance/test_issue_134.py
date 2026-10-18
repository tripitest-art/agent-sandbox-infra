import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick"] = r
_spec.loader.exec_module(r)

SHA = "a" * 40
LABEL = "review-anfordern"
OK_REVIEW = "**Freigegeben**\n\n### Muss\nkeine\n\n### Kann\n- Import nach oben\n\n### Geprüft\n- alles"
CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": "2026-10-16T13:29:10Z"}
          for n in ("tests", "acceptance-guard")]
KEIN_ISSUE = "Freigegeben, kein Worker-Issue: bitte prüfen und mergen."


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    p = r.projects.default_projects()[0]
    monkeypatch.setattr(r, "PROJECTS", [p])
    monkeypatch.setattr(r, "REPO", p.repo)
    monkeypatch.setattr(r, "REVIEW_SKILL", p.review_skill)
    monkeypatch.setattr(r, "PROJECT_AUTO_MERGE", True)
    monkeypatch.setattr(r, "PROJECT_MANUAL_PATHS", ("AGENTS.md", ".github/*"))
    monkeypatch.setattr(r, "REQUIRED_CHECKS", ("tests", "acceptance-guard"))
    monkeypatch.setattr(r, "BASE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "BASE_DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "REVIEW_KEY", "test-key")
    monkeypatch.setattr(r, "REVIEWER_NAME", "GLM")
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "coder-glm")
    monkeypatch.setattr(r, "POST", True)
    monkeypatch.setattr(r, "AUTO_MERGE", True)

    def no_network(*a, **k):
        raise urllib.error.URLError("kein Netz im Test")
    monkeypatch.setattr(r.urllib.request, "urlopen", no_network)


def _takt(monkeypatch, body, files=("docs/a.md",), branch="feature/dashboard-statistik", issue_body=None):
    """Ein Takt mit freigegebenem PR #7. Ohne `issue_body` hat der PR kein Worker-Issue (Label-PR)."""
    seen = {"merges": [], "reasons": []}
    pr = {"number": 7, "title": "Titel", "body": "Was und warum\n\n" + body + "\nWorker: Claude Sonnet 5.5\n",
          "headRefName": branch, "headRefOid": SHA, "isCrossRepository": False, "isDraft": False,
          "labels": [{"name": LABEL}], "reviews": [], "commits": [{"committedDate": "2026-10-16T10:00:00Z"}],
          "statusCheckRollup": CHECKS}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": f, "additions": 1, "deletions": 0} for f in files]}
        if args[:2] == ("issue", "list"):
            return []
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "Issue", "body": issue_body or ""}
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/x b/x\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", lambda model, messages: (OK_REVIEW, {"prompt_tokens": 1, "completion_tokens": 1}))
    monkeypatch.setattr(r, "post_review", lambda n, t: None)
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: True)
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: seen["merges"].append(n) or True)
    monkeypatch.setattr(r, "to_stephan", lambda n, key, reason, state: seen["reasons"].append(reason))
    assert r.run_once() == 0
    return seen


def test_label_pr_mit_merge_modus_automatisch_wird_gemergt(monkeypatch):
    seen = _takt(monkeypatch, "Merge-Modus: automatisch\n")
    assert seen["merges"] == [7] and seen["reasons"] == []


def test_label_pr_ohne_merge_modus_zeile_geht_an_stephan(monkeypatch):
    seen = _takt(monkeypatch, "")
    assert seen["merges"] == [] and seen["reasons"] == [KEIN_ISSUE]


def test_label_pr_mit_merge_modus_manuell_geht_an_stephan(monkeypatch):
    seen = _takt(monkeypatch, "Merge-Modus: manuell durch Stephan\n")
    assert seen["merges"] == [] and seen["reasons"] == [KEIN_ISSUE]


def test_label_pr_mit_aufsichtsdatei_wird_trotz_automatisch_nicht_gemergt(monkeypatch):
    seen = _takt(monkeypatch, "Merge-Modus: automatisch\n", files=("docs/a.md", "AGENTS.md"))
    assert seen["merges"] == []
    assert seen["reasons"] == ["Freigegeben, berührt Dateien mit manuellem Merge (AGENTS.md): bitte prüfen und mergen."]


def test_label_pr_in_einem_projekt_mit_auto_merge_false_wird_nicht_gemergt(monkeypatch):
    monkeypatch.setattr(r, "PROJECT_AUTO_MERGE", False)
    seen = _takt(monkeypatch, "Merge-Modus: automatisch\n")
    assert seen["merges"] == [] and seen["reasons"] == [KEIN_ISSUE]


def test_bei_einem_worker_pr_gilt_der_issue_text_nicht_der_pr_text(monkeypatch):
    # PR-Text sagt „automatisch“, das Issue (der Vertrag) sagt „manuell“: es bleibt bei „manuell“.
    seen = _takt(monkeypatch, "Merge-Modus: automatisch\n", branch="feature/issue-5",
                 issue_body="### Merge-Modus\n\nmanuell durch Stephan\n")
    assert seen["merges"] == []
    assert seen["reasons"] == ["Freigegeben, Merge-Modus manuell: bitte prüfen und mergen."]
