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
CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"} for n in ("tests", "acceptance-guard")]


def _kein_issue(*args):
    pytest.fail("zu diesem PR gibt es kein Worker-Issue")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    # Wie in tests/test_review_tick.py: feste Vorgaben, unabhängig von Server-Projektliste und -Umgebung.
    p = r.projects.default_projects()[0]
    monkeypatch.setattr(r, "PROJECTS", [p])
    monkeypatch.setattr(r, "REPO", p.repo)
    monkeypatch.setattr(r, "REVIEW_SKILL", p.review_skill)
    monkeypatch.setattr(r, "PROJECT_AUTO_MERGE", True)
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

    def no_network(*a, **k):
        raise urllib.error.URLError("kein Netz im Test")
    monkeypatch.setattr(r.urllib.request, "urlopen", no_network)


def _pr(number, branch, labels=(), **kw):
    pr = {"number": number, "title": "Dashboard: Statistik", "body": "Was und warum\n\nWorker: Claude Sonnet 5.5\n",
          "headRefName": branch, "headRefOid": SHA, "isCrossRepository": False, "isDraft": False,
          "labels": [{"name": n} for n in labels], "reviews": [],
          "commits": [{"committedDate": "2026-10-16T10:00:00Z"}], "statusCheckRollup": CHECKS}
    pr.update(kw)
    return pr


def test_label_konstante():
    assert r.REVIEW_REQUEST_LABEL == LABEL


def test_pick_pr_nimmt_ein_gelabeltes_pr_mit_beliebigem_branch():
    got = r.pick_pr([_pr(111, "feature/dashboard-budget", [LABEL])], {}, _kein_issue)
    assert got["number"] == 111 and got["issue_no"] is None


def test_pick_pr_laesst_ein_pr_ohne_label_und_ohne_issue_branch_liegen():
    assert r.pick_pr([_pr(112, "feature/dashboard-push")], {}, _kein_issue) is None


def test_pick_pr_prueft_auch_mit_label_keine_forks_drafts_leere_und_geprueften_prs():
    fork = _pr(1, "feature/a", [LABEL], isCrossRepository=True)
    draft = _pr(2, "feature/b", [LABEL], isDraft=True)
    leer = _pr(3, "feature/c", [LABEL], commits=[])
    geprueft = _pr(4, "feature/d", [LABEL], reviews=[
        {"author": {"login": r.OWNER}, "submittedAt": "2026-10-16T11:00:00Z", "commit": {"oid": SHA}}])
    assert r.pick_pr([fork, draft, leer, geprueft], {}, _kein_issue) is None
    assert r.pick_pr([_pr(5, "feature/e", [LABEL])], {"erledigt": [f"pr5@{SHA}"]}, _kein_issue) is None


def test_pick_pr_behaelt_den_weg_ueber_das_worker_issue():
    assert r.pick_pr([_pr(6, "feature/issue-6")], {}, lambda n: ["agent-qwen"])["issue_no"] == 6
    assert r.pick_pr([_pr(6, "feature/issue-6")], {}, lambda n: ["idee"]) is None
    # Mit Label zählt der PR auch dann, wenn das Issue kein Worker-Label trägt; die Issue-Nummer bleibt.
    assert r.pick_pr([_pr(7, "feature/issue-7", [LABEL])], {}, lambda n: ["idee"])["issue_no"] == 7


def test_build_messages_ohne_issue_sagt_es_und_laesst_den_issue_block_weg():
    msgs = r.build_messages(None, _pr(111, "feature/x", [LABEL]), [{"path": "a.py", "additions": 1, "deletions": 0}],
                            "diff --git a/a.py b/a.py\n+x\n", [("tests", "SUCCESS")], "Regeln")
    user = msgs[1]["content"]
    assert "PR #111" in user and "ohne Worker-Issue" in user and "diff --git a/a.py" in user
    assert "----- ISSUE -----" not in user and "None" not in user


def test_review_per_label_postet_und_uebergibt_den_merge_an_stephan(monkeypatch):
    monkeypatch.setattr(r, "POST", True)
    seen = {"posted": [], "reasons": [], "merges": [], "messages": None}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            felder = args[args.index("--json") + 1].split(",")
            assert "labels" in felder          # ohne die Label-Liste sieht der Takt die Anforderung nicht
            return [_pr(111, "feature/dashboard-budget", [LABEL])]
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 3, "deletions": 0}]}
        if args[:2] == ("issue", "list"):
            return []
        _kein_issue()

    def call_llm(model, messages):
        seen["messages"] = messages
        return OK_REVIEW, {"prompt_tokens": 1000, "completion_tokens": 100}

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/a.py b/a.py\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", call_llm)
    monkeypatch.setattr(r, "post_review", lambda n, t: seen["posted"].append(n))
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: True)
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: seen["merges"].append(n) or True)
    monkeypatch.setattr(r, "to_stephan", lambda n, key, reason, state: seen["reasons"].append((n, reason)))
    assert r.run_once() == 0
    assert seen["posted"] == [111]
    assert "ohne Worker-Issue" in seen["messages"][1]["content"]
    assert seen["merges"] == []                 # ohne Issue gibt es keinen Merge-Modus „automatisch“
    assert seen["reasons"] == [(111, "Freigegeben, kein Worker-Issue: bitte prüfen und mergen.")]
