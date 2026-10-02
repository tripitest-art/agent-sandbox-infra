"""Tests für sandbox/review_tick.py (Review-Takt für Worker-PRs), ohne Netzwerk und ohne gh."""

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick"] = r
_spec.loader.exec_module(r)

OK_REVIEW = "**Freigegeben**\n\n### Muss\nkeine\n\n### Kann\n- Import nach oben\n- Typparameter\n\n### Geprüft\n- alles"
BAD_REVIEW = "**Änderungen nötig**\n\n### Muss\n1. `a.py` Zeile 3: Signatur weicht von „Schnittstellen“ ab.\n\n### Kann\n- keine"
GOOD_CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"} for n in ("tests", "acceptance-guard", "CodeQL")]
SHA = "a" * 40


def _pr(number=7, **kw):
    pr = {"number": number, "title": "Titel", "body": "Closes #5\n\nWorker: DeepSeek (LiteLLM coder-fast)\n",
          "headRefName": "feature/issue-5", "headRefOid": SHA, "isCrossRepository": False, "reviews": [],
          "commits": [{"committedDate": "2026-10-02T10:00:00Z"}], "statusCheckRollup": GOOD_CHECKS}
    pr.update(kw)
    return pr


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "REVIEW_KEY", "test-key")
    monkeypatch.setattr(r, "REVIEWER_NAME", "GLM")
    monkeypatch.setattr(r, "POST", False)

    def no_network(*a, **k):
        raise urllib.error.URLError("kein Netz im Test")
    monkeypatch.setattr(r.urllib.request, "urlopen", no_network)


def test_extract_section_by_heading_level():
    md = "# T\n## A\na1\n### A.1\na2\n## B\nb1\n"
    assert r.extract_section(md, "## A") == "## A\na1\n### A.1\na2"
    assert r.extract_section(md, "### A.1") == "### A.1\na2"
    assert r.extract_section(md, "## X") == ""


def test_extract_section_until_spans_several_steps():
    md = "## Schritt 4\nx\n## Schritt 5\nf\n## Schritt 6\nd\n## Schritt 7\nposten\n"
    assert r.extract_section(md, "## Schritt 5", until=("## Schritt 7",)) == "## Schritt 5\nf\n## Schritt 6\nd"


def test_parse_review_counts_and_consistency():
    assert r.parse_review(OK_REVIEW) == ("**Freigegeben**", 0, 2, None)
    verdict, muss, kann, problem = r.parse_review(BAD_REVIEW)
    assert (verdict, muss, kann, problem) == ("**Änderungen nötig**", 1, 0, None)


def test_parse_review_flags_inconsistent_or_unparsable():
    assert r.parse_review("**Freigegeben**\n\n### Muss\n1. etwas\n\n### Kann\n- x")[3]
    assert r.parse_review("**Änderungen nötig**\n\n### Muss\n- keine\n\n### Kann\n- x")[3]
    assert r.parse_review("Hier mein Review: alles gut")[0] is None


def test_worker_family_and_same_family():
    assert r.model_family("DeepSeek (LiteLLM coder-fast)") == "deepseek"
    assert r.model_family("GLM 5.3 Flash (LiteLLM coder-glm)") == "glm"
    assert r.model_family("Qwen3-Coder 30B") == "qwen"
    assert r.model_family("coder-fast (LiteLLM)") == "deepseek"   # so steht es bei unbekannten Aliasen im PR
    assert r.model_family("Codestral (LiteLLM coder-free)") == "mistral"
    assert r.model_family("unbekannt") is None
    assert r.worker_from_body("## X\n<!-- Worker: Kommentar -->\n**Worker:** Gemini Flash / Review: x") == "Gemini Flash"
    assert r.worker_from_body("kein Hinweis") is None


def test_checks_summary_pending_and_red():
    done, rows, red = r.checks_summary(GOOD_CHECKS)
    assert done and not red and ("tests", "SUCCESS") in rows
    pending = GOOD_CHECKS + [{"name": "Analyze (python)", "status": "IN_PROGRESS", "conclusion": ""}]
    assert r.checks_summary(pending)[0] is False
    red_in = [dict(GOOD_CHECKS[0], conclusion="FAILURE")] + GOOD_CHECKS[1:]
    assert r.checks_summary(red_in)[2] == ["tests"]
    # github-advanced-security (StatusContext) ist kein Pflicht-Check
    ctx = GOOD_CHECKS + [{"context": "github-advanced-security", "state": "FAILURE"}]
    assert r.checks_summary(ctx)[2] == []


def test_assemble_sets_model_line_and_check_line_itself():
    raw = OK_REVIEW.replace("\n\n### Muss", "\n\nReview nach AGENTS.md → Reviewer. Worker: x, Reviewer: Claude.\n- Checks: alles grün\n\n### Muss", 1)
    out = r.assemble(raw, "DeepSeek", [("tests", "SUCCESS"), ("CodeQL", "FAILURE")])
    assert out.splitlines()[0] == "**Freigegeben**"
    assert "Worker: DeepSeek, Reviewer: GLM (andere Modellfamilie)." in out
    assert "Claude" not in out and "alles grün" not in out
    assert "- Checks (laut Check-Runs): tests ✅, CodeQL FAILURE" in out


def test_pick_pr_skips_forks_wrong_label_and_reviewed():
    labels = {5: ["agent-qwen"], 6: ["agent-qwen"], 7: ["agent-antigravity"], 8: ["agent-qwen"], 9: ["agent-qwen"]}
    fork = _pr(6, headRefName="feature/issue-6", isCrossRepository=True)
    wrong = _pr(7, headRefName="feature/issue-7")
    reviewed = _pr(8, headRefName="feature/issue-8", reviews=[
        {"author": {"login": r.OWNER}, "submittedAt": "2026-10-02T11:00:00Z", "commit": {"oid": SHA}}])
    stale = _pr(9, headRefName="feature/issue-9", reviews=[
        {"author": {"login": r.OWNER}, "submittedAt": "2026-10-02T11:00:00Z", "commit": {"oid": "b" * 40}}])
    other = _pr(10, headRefName="fix/etwas")
    got = r.pick_pr([other, wrong, fork, reviewed, stale], {}, lambda n: labels[n])
    assert got["number"] == 9 and got["issue_no"] == 9   # Review galt einem älteren Commit
    assert r.pick_pr([_pr(5)], {"erledigt": [f"pr5@{SHA}"]}, lambda n: labels[5]) is None


def _fake_github(monkeypatch, prs, llm_text=OK_REVIEW, calls=None):
    calls = calls if calls is not None else {}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return prs
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "Das Issue", "body": "Betroffene Dateien: a.py"}
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 3, "deletions": 0}]}
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/a.py b/a.py\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx" if "AGENTS" in p else "## Schritt 5\nf\n## Schritt 6\nd\n## Schritt 7\nx")

    def call_llm(model, messages):
        calls["llm"] = calls.get("llm", 0) + 1
        calls["model"] = model
        calls["system"] = messages[0]["content"]
        return llm_text, {"prompt_tokens": 1000, "completion_tokens": 100}
    monkeypatch.setattr(r, "call_llm", call_llm)
    monkeypatch.setattr(r, "post_review", lambda n, t: calls.setdefault("posted", []).append(n))
    return calls


def test_dry_run_writes_draft_and_does_not_post(monkeypatch, capsys, tmp_path):
    calls = _fake_github(monkeypatch, [_pr()])
    assert r.run_once() == 0
    assert calls["llm"] == 1 and calls["model"] == "coder-glm" and "posted" not in calls
    draft = (tmp_path / "reviews" / f"pr7-{SHA[:7]}.md").read_text(encoding="utf-8")
    assert "Reviewer: GLM" in draft and "Worker: DeepSeek (LiteLLM coder-fast)" in draft
    assert "REVIEW_POST=0, nicht gepostet" in capsys.readouterr().out
    assert "Keine Werkzeuge" not in calls["system"] and "Goldene Regeln" in calls["system"]
    # zweiter Takt: nichts mehr zu tun
    r.run_once()
    assert calls["llm"] == 1


def test_post_mode_posts_comment_review(monkeypatch):
    monkeypatch.setattr(r, "POST", True)
    calls = _fake_github(monkeypatch, [_pr()])
    r.run_once()
    assert calls["posted"] == [7]


def test_red_check_needs_no_model_and_counts_a_round(monkeypatch, tmp_path):
    red = [dict(GOOD_CHECKS[0], conclusion="FAILURE")] + GOOD_CHECKS[1:]
    calls = _fake_github(monkeypatch, [_pr(statusCheckRollup=red)])
    r.run_once()
    assert "llm" not in calls
    text = next((tmp_path / "reviews").glob("pr7-*.md")).read_text(encoding="utf-8")
    assert "**Änderungen nötig**" in text and "`tests` ist nicht grün" in text
    assert json.loads((tmp_path / "state.json").read_text())["runden"] == {"7": 1}


def test_pending_checks_and_missing_worker_line_wait(monkeypatch):
    pending = GOOD_CHECKS + [{"name": "Analyze (python)", "status": "IN_PROGRESS", "conclusion": ""}]
    calls = _fake_github(monkeypatch, [_pr(statusCheckRollup=pending), _pr(8, headRefName="feature/issue-8", body="Closes #8")])
    r.run_once()
    assert "llm" not in calls


def test_same_family_is_skipped(monkeypatch, capsys):
    monkeypatch.setattr(r, "REVIEWER_NAME", "DeepSeek")
    calls = _fake_github(monkeypatch, [_pr()])
    r.run_once()
    assert "llm" not in calls and "gleiche Modellfamilie" in capsys.readouterr().out


def test_unusable_answer_is_retried_once_then_dropped(monkeypatch, capsys):
    calls = _fake_github(monkeypatch, [_pr()], llm_text="Mein Review: sieht gut aus")
    r.run_once(); r.run_once(); r.run_once()
    assert calls["llm"] == 2   # zwei Versuche, dann gibt der Takt auf
    assert "bitte von Hand prüfen" in capsys.readouterr().out


def test_max_rounds_leave_pr_to_stephan(monkeypatch, capsys, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"runden": {"7": r.MAX_ROUNDS}}))
    calls = _fake_github(monkeypatch, [_pr()])
    r.run_once()
    assert "llm" not in calls and "liegt bei Stephan" in capsys.readouterr().out


def test_missing_key_does_nothing(monkeypatch, capsys):
    monkeypatch.setattr(r, "REVIEW_KEY", "")
    calls = _fake_github(monkeypatch, [_pr()])
    r.run_once()
    assert "llm" not in calls and "LITELLM_REVIEW_KEY fehlt" in capsys.readouterr().out
