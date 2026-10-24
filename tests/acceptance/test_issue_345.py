import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_akz_diff", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_akz_diff"] = r
_spec.loader.exec_module(r)

TEST_CODE = "def test_x():\n    assert 1 + 1 == 2\n"
ISSUE_BODY = ("### Ziel\n\nX.\n\n### Akzeptanztests\n\n```python\n" + TEST_CODE + "```\n\n"
              "### Fertig, wenn\n\n- [ ] grün\n")
CODE_TEIL = ("diff --git a/sandbox/a.py b/sandbox/a.py\nindex 1..2 100644\n--- a/sandbox/a.py\n"
             "+++ b/sandbox/a.py\n@@ -1 +1 @@\n-alt\n+neu\n")


def _akz_teil(n, code=TEST_CODE, neu=True):
    kopf = f"diff --git a/tests/acceptance/test_issue_{n}.py b/tests/acceptance/test_issue_{n}.py\n"
    if neu:
        kopf += "new file mode 100644\nindex 0000000..1111111\n--- /dev/null\n"
    else:
        kopf += f"index 1111111..2222222 100644\n--- a/tests/acceptance/test_issue_{n}.py\n"
    zeilen = code.rstrip("\n").split("\n")
    kopf += f"+++ b/tests/acceptance/test_issue_{n}.py\n@@ -0,0 +1,{len(zeilen)} @@\n"
    return kopf + "".join(f"+{z}\n" for z in zeilen)


def test_neue_wortgleiche_kopie_wird_ersetzt():
    diff = CODE_TEIL + _akz_teil(6)
    out = r.drop_acceptance_from_diff(diff, 6, ISSUE_BODY)
    assert CODE_TEIL in out
    assert r.ACCEPTANCE_OMITTED.format(n=6) in out
    assert "assert 1 + 1 == 2" not in out
    assert len(out) < len(diff)


def test_abweichung_geaenderte_datei_oder_fremde_nummer_bleiben():
    abweichend = CODE_TEIL + _akz_teil(6, code="def test_x():\n    assert True\n")
    assert r.drop_acceptance_from_diff(abweichend, 6, ISSUE_BODY) == abweichend
    geaendert = CODE_TEIL + _akz_teil(6, neu=False)
    assert r.drop_acceptance_from_diff(geaendert, 6, ISSUE_BODY) == geaendert
    fremd = CODE_TEIL + _akz_teil(7)
    assert r.drop_acceptance_from_diff(fremd, 6, ISSUE_BODY) == fremd


def test_ohne_issue_oder_block_unveraendert():
    diff = CODE_TEIL + _akz_teil(6)
    assert r.drop_acceptance_from_diff(diff, None, ISSUE_BODY) == diff
    assert r.drop_acceptance_from_diff(diff, 6, None) == diff
    assert r.drop_acceptance_from_diff(diff, 6, "### Ziel\n\nohne Block\n") == diff
    assert r.drop_acceptance_from_diff("", 6, ISSUE_BODY) == ""


def test_review_bekommt_den_gekuerzten_diff(monkeypatch, tmp_path):
    p = r.projects.default_projects()[0]
    monkeypatch.setattr(r, "PROJECTS", [p])
    monkeypatch.setattr(r, "REPO", p.repo)
    monkeypatch.setattr(r, "REVIEW_SKILL", p.review_skill)
    monkeypatch.setattr(r, "BASE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "BASE_DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "REVIEW_KEY", "test-key")
    monkeypatch.setattr(r, "REVIEWER_NAME", "GLM")
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "coder-glm")
    monkeypatch.setattr(r, "POST", False)

    def kein_netz(*a, **k):
        raise urllib.error.URLError("kein Netz im Test")
    monkeypatch.setattr(r.urllib.request, "urlopen", kein_netz)

    checks = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"} for n in ("tests", "acceptance-guard")]
    pr = {"number": 41, "title": "T", "body": "Closes #6\n\nWorker: DeepSeek (LiteLLM coder)\n",
          "headRefName": "feature/issue-6", "headRefOid": "a" * 40, "isCrossRepository": False,
          "isDraft": False, "labels": [], "reviews": [],
          "commits": [{"committedDate": "2026-10-24T10:00:00Z"}], "statusCheckRollup": checks}
    seen = {}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "sandbox/a.py", "additions": 1, "deletions": 1}]}
        if args[:2] == ("issue", "view"):
            if "number,title,body" in args:
                return {"number": 6, "title": "Issue 6", "body": ISSUE_BODY}
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "list"):
            return []
        pytest.fail(f"unerwarteter gh-Aufruf: {args}")

    def call_llm(model, messages):
        seen["user"] = messages[1]["content"]
        return "**Freigegeben**\n\n### Muss\nkeine\n\n### Kann\n- x\n\n### Geprüft\n- y", {"completion_tokens": 9}

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: CODE_TEIL + _akz_teil(6))
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", call_llm)
    monkeypatch.setattr(r, "post_review", lambda n, t: None)
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: True)
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: True)
    monkeypatch.setattr(r, "to_stephan", lambda n, key, reason, state: None)
    assert r.run_once() == 0
    assert r.ACCEPTANCE_OMITTED.format(n=6) in seen["user"]
    assert seen["user"].count("assert 1 + 1 == 2") == 1
    assert "+neu" in seen["user"]
