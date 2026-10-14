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


def test_suspicious_output_accepts_normal_german_review():
    assert r.suspicious_output(OK_REVIEW + "\n- Größe, Straße, über „Schnittstellen“ ✅ −8 %", {"completion_tokens": 900, "finish_reason": "stop"}) is None


def test_suspicious_output_rejects_token_limit_and_garbage():
    assert r.suspicious_output(OK_REVIEW, {"completion_tokens": r.MAX_TOKENS, "finish_reason": "stop"})
    assert r.suspicious_output(OK_REVIEW, {"completion_tokens": 100, "finish_reason": "length"})
    garbage = OK_REVIEW + "\n" + "正规 modular同等 tracking 报告链路 машины " * 5
    assert r.suspicious_output(garbage, {"completion_tokens": 300, "finish_reason": "stop"})


def test_garbage_after_valid_start_is_not_saved_as_review(monkeypatch, tmp_path):
    """Regression 02.10.2026: Zeichensalat hinter „**Freigegeben**“ darf kein Entwurf werden."""
    garbage = OK_REVIEW + "\n" + "正规 modular同等 tracking 报告链路 машины " * 20
    monkeypatch.setattr(r, "gh_json", lambda *a, **k: [_pr()] if a[:2] == ("pr", "list") else
                        ({"labels": [{"name": "agent-qwen"}]} if a[:2] == ("issue", "view") and "labels" in a else
                         ({"files": []} if a[:2] == ("pr", "view") else {"number": 5, "title": "T", "body": "B"})))
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/x b/x\n")
    monkeypatch.setattr(r, "repo_file", lambda path: "## Goldene Regeln\nx\n### Reviewer\ny\n")
    monkeypatch.setattr(r, "call_llm", lambda model, messages: (garbage, {"completion_tokens": r.MAX_TOKENS, "finish_reason": "length"}))
    r.run_once()
    state = json.loads(Path(r.STATE).read_text())
    assert state["fehler"][f"pr7@{SHA}"] == 1
    assert not list(Path(r.DRAFT_DIR).glob("*.md")) if Path(r.DRAFT_DIR).exists() else True


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
    labels = {5: ["agent-qwen"], 6: ["agent-qwen"], 7: ["idee"], 8: ["agent-qwen"], 9: ["agent-qwen"]}
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


def test_is_auto_merge():
    # Standard-Formular nach worker-task.yml
    assert r.is_auto_merge("### Merge-Modus\n\nautomatisch nach Review\n\n### Kontext\nkein") is True
    assert r.is_auto_merge("### Merge-Modus\n\nmanuell durch Stephan (Betrieb)\n\n### Kontext\nkein") is False
    # Kurze Form
    assert r.is_auto_merge("### Merge-Modus\nautomatisch") is True
    assert r.is_auto_merge("## Merge-Modus\nautomatisch") is True
    assert r.is_auto_merge("### Merge-Modus\nmanuell") is False
    # Inline
    assert r.is_auto_merge("**Merge-Modus:** automatisch") is True
    assert r.is_auto_merge("Merge-Modus: manuell") is False
    # Fehlt oder unklar -> False
    assert r.is_auto_merge("### Ziel\nEtwas bauen") is False
    assert r.is_auto_merge("") is False
    assert r.is_auto_merge(None) is False


def test_parse_dependencies():
    assert r.parse_dependencies("### Kontext\nWartet auf: #189\nPlaner: Antigravity") == [189]
    assert r.parse_dependencies("Wartet auf: #189, #203") == [189, 203]
    assert r.parse_dependencies("- Wartet auf: #189 und #203") == [189, 203]
    assert r.parse_dependencies("**Wartet auf:** #189, #203") == [189, 203]
    assert r.parse_dependencies("Wartet auf: https://github.com/tripitest-art/sc-digger/pull/219") == [219]
    assert r.parse_dependencies("Wartet auf: #10\nWartet auf: #10, #20") == [10, 20]
    assert r.parse_dependencies("Wartet auf: keine") == []
    assert r.parse_dependencies("Keine Abhängigkeiten") == []
    assert r.parse_dependencies(None) == []


def test_is_dependency_resolved(monkeypatch):
    states = {100: "CLOSED", 101: "MERGED", 102: "OPEN"}
    monkeypatch.setattr(r, "gh_json", lambda *a, **k: {"state": states.get(int(a[2]), "OPEN")})
    cache = {}
    assert r.is_dependency_resolved(100, cache) is True
    assert r.is_dependency_resolved(101, cache) is True
    assert r.is_dependency_resolved(102, cache) is False
    assert cache == {100: "CLOSED", 101: "MERGED", 102: "OPEN"}
    # Cache wird verwendet (ohne gh_json aufzurufen)
    monkeypatch.setattr(r, "gh_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("darf nicht aufgerufen werden")))
    assert r.is_dependency_resolved(100, cache) is True


def test_unblock_dependent_issues_dry_run_vs_post(monkeypatch, capsys):
    blocked_issues = [
        {"number": 202, "title": "Aufgabe 202", "body": "### Kontext\nWartet auf: #189"},
        {"number": 203, "title": "Aufgabe 203", "body": "### Kontext\nWartet auf: #190"},
        {"number": 204, "title": "Aufgabe 204", "body": "Keine Abhängigkeit genannt"},
    ]
    states = {189: "MERGED", 190: "OPEN"}
    edits = []

    def mock_gh_json(*args, **kw):
        if args[:2] == ("issue", "list"):
            return blocked_issues
        if args[:2] == ("issue", "view"):
            return {"state": states.get(int(args[2]), "OPEN")}
        raise AssertionError(args)

    def mock_gh_text(*args, **kw):
        if args[:2] == ("issue", "edit"):
            edits.append(list(args))
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", mock_gh_json)
    monkeypatch.setattr(r, "gh_text", mock_gh_text)

    # 1. Dry Run (POST=False): Findet #202 als auflösbar, editiert aber nicht
    monkeypatch.setattr(r, "POST", False)
    res = r.unblock_dependent_issues()
    assert res == []
    assert edits == []
    assert "REVIEW_POST=0, Entblocken übersprungen" in capsys.readouterr().out

    # 2. Live (POST=True): Setzt #202 auf bereit
    monkeypatch.setattr(r, "POST", True)
    res = r.unblock_dependent_issues()
    assert res == [202]
    assert len(edits) == 1
    assert edits[0] == ["issue", "edit", "202", "--add-label", "bereit", "--remove-label", "blockiert"]


def test_auto_merge_when_approved_and_auto_mode(monkeypatch):
    monkeypatch.setattr(r, "POST", True)
    monkeypatch.setattr(r, "AUTO_MERGE", True)
    calls = _fake_github(monkeypatch, [_pr()])

    def mock_gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [_pr()]
        if args[:2] == ("issue", "list"):
            return []
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "T", "body": "### Merge-Modus\nautomatisch nach Review"}
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 1, "deletions": 0}]}
        raise AssertionError(args)

    merged = []
    def mock_gh_text(*args, **kw):
        if args[:2] == ("pr", "merge"):
            merged.append(args[2])
            return ""
        if args[:2] == ("pr", "diff"):
            return "diff --git a/a.py b/a.py\n+x\n"
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", mock_gh_json)
    monkeypatch.setattr(r, "gh_text", mock_gh_text)

    r.run_once()
    assert calls.get("posted") == [7]
    assert merged == ["7"]


def test_no_auto_merge_when_manual_mode(monkeypatch, capsys):
    monkeypatch.setattr(r, "POST", True)
    monkeypatch.setattr(r, "AUTO_MERGE", True)
    calls = _fake_github(monkeypatch, [_pr()])

    def mock_gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [_pr()]
        if args[:2] == ("issue", "list"):
            return []
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "T", "body": "### Merge-Modus\nmanuell durch Stephan (Betrieb)"}
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 1, "deletions": 0}]}
        raise AssertionError(args)

    merged = []
    def mock_gh_text(*args, **kw):
        if args[:2] == ("pr", "merge"):
            merged.append(args[2])
            return ""
        if args[:2] == ("pr", "diff"):
            return "diff --git a/a.py b/a.py\n+x\n"
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", mock_gh_json)
    monkeypatch.setattr(r, "gh_text", mock_gh_text)

    r.run_once()
    assert calls.get("posted") == [7]
    assert merged == []
    assert "Merge-Modus manuell; liegt bei Stephan" in capsys.readouterr().out


def test_no_auto_merge_when_env_disabled(monkeypatch, capsys):
    monkeypatch.setattr(r, "POST", True)
    monkeypatch.setattr(r, "AUTO_MERGE", False)
    calls = _fake_github(monkeypatch, [_pr()])

    def mock_gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [_pr()]
        if args[:2] == ("issue", "list"):
            return []
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "T", "body": "### Merge-Modus\nautomatisch"}
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 1, "deletions": 0}]}
        raise AssertionError(args)

    merged = []
    def mock_gh_text(*args, **kw):
        if args[:2] == ("pr", "merge"):
            merged.append(args[2])
            return ""
        if args[:2] == ("pr", "diff"):
            return "diff --git a/a.py b/a.py\n+x\n"
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", mock_gh_json)
    monkeypatch.setattr(r, "gh_text", mock_gh_text)

    r.run_once()
    assert calls.get("posted") == [7]
    assert merged == []
    assert "REVIEW_AUTO_MERGE=0; kein Merge" in capsys.readouterr().out



# --- Durchgängiger Takt: Entwürfe nachholen, Merge wiederholen, an Stephan melden ---

def test_pick_pr_takes_every_worker_family_label():
    for label in ("agent-qwen", "agent-antigravity", "agent-claude"):
        assert r.pick_pr([_pr()], {}, lambda n: [label])["number"] == 7
    assert r.pick_pr([_pr()], {}, lambda n: ["worker-task"]) is None


def _github_with_merge(monkeypatch, merge_mode="automatisch", merge_ok=True, pr_view=None, prs=None):
    """GitHub-Fake mit Merge, Label, Kommentar; gibt die Aufrufliste zurück."""
    calls = _fake_github(monkeypatch, prs if prs is not None else [_pr()])
    calls.update(merged=[], edits=[], comments=[], labels=[])
    base_json = r.gh_json

    def gh_json(*args):
        if args[:2] == ("issue", "list"):
            return []
        if args[:2] == ("issue", "view") and "labels" not in args and "state" not in args:
            return {"number": 5, "title": "T", "body": f"### Merge-Modus\n{merge_mode}"}
        if args[:2] == ("pr", "view") and "mergeStateStatus" in args[-1]:
            return pr_view
        return base_json(*args)

    def gh_text(*args, **kw):
        if args[:2] == ("pr", "merge"):
            calls["merged"].append(args[2])
            if not merge_ok:
                raise r.subprocess.CalledProcessError(1, "gh", stderr="not mergeable")
            return ""
        if args[:2] == ("pr", "edit"):
            calls["edits"].append(args[2:])
            return ""
        if args[:2] == ("pr", "comment"):
            calls["comments"].append(args[2])
            return ""
        if args[:2] == ("label", "create"):
            calls["labels"].append(args[2])
            return ""
        if args[:2] == ("pr", "diff"):
            return "diff --git a/a.py b/a.py\n+x\n"
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", gh_text)
    return calls


def test_draft_from_dry_run_is_posted_later_without_new_model_call(monkeypatch, tmp_path, capsys):
    calls = _github_with_merge(monkeypatch)
    r.run_once()                                   # REVIEW_POST=0: nur Entwurf
    assert calls["llm"] == 1 and "posted" not in calls and calls["merged"] == []
    monkeypatch.setattr(r, "POST", True)            # jetzt scharf geschaltet
    r.run_once()
    assert calls["llm"] == 1                        # kein zweiter Modellaufruf
    assert calls["posted"] == [7] and calls["merged"] == ["7"]
    assert "ohne neuen Modellaufruf" in capsys.readouterr().out
    r.run_once()                                    # danach nichts mehr
    assert calls["posted"] == [7] and calls["llm"] == 1


def test_draft_with_problem_is_not_reused(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "POST", True)
    d = tmp_path / "reviews"; d.mkdir()
    (d / f"pr7-{SHA[:7]}.md").write_text("<!-- PR #7 PROBLEM: x -->\n" + OK_REVIEW, encoding="utf-8")
    assert r.pending_draft(_pr()) is None
    (d / f"pr7-{SHA[:7]}.md").write_text("<!-- PR #7 -->\nkein Urteil", encoding="utf-8")
    assert r.pending_draft(_pr()) is None
    (d / f"pr7-{SHA[:7]}.md").write_text("<!-- PR #7 -->\n" + OK_REVIEW, encoding="utf-8")
    assert r.pending_draft(_pr()) == OK_REVIEW
    assert r.pending_draft(_pr(headRefOid="b" * 40)) is None    # anderer Commit


def test_draft_of_second_round_is_still_posted(monkeypatch, tmp_path):
    """Die Runde wurde beim Entwurf schon gezählt; MAX_ROUNDS darf das Posten nicht verhindern."""
    calls = _github_with_merge(monkeypatch)
    calls_llm = calls
    monkeypatch.setattr(r, "call_llm", lambda m, msg: (calls_llm.__setitem__("llm", calls_llm.get("llm", 0) + 1) or BAD_REVIEW,
                                                      {"prompt_tokens": 1, "completion_tokens": 1}))
    (tmp_path / "state.json").write_text(json.dumps({"runden": {"7": r.MAX_ROUNDS - 1}}))
    r.run_once()
    assert json.loads((tmp_path / "state.json").read_text())["runden"] == {"7": r.MAX_ROUNDS}
    monkeypatch.setattr(r, "POST", True)
    r.run_once()
    assert calls["posted"] == [7] and calls["llm"] == 1 and calls["merged"] == []


def test_failed_merge_is_retried_next_tick(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "POST", True)
    calls = _github_with_merge(monkeypatch, merge_ok=False,
                               pr_view={"state": "OPEN", "headRefOid": SHA, "mergeStateStatus": "BLOCKED"})
    r.run_once()
    assert calls["merged"] == ["7"]
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["merge_offen"] == {"7": {"sha": SHA, "versuche": 0}}
    # nächster Takt: jetzt mergebar
    calls2 = _github_with_merge(monkeypatch, merge_ok=True,
                                pr_view={"state": "OPEN", "headRefOid": SHA, "mergeStateStatus": "CLEAN"})
    r.run_once()
    assert calls2["merged"] == ["7"] and calls2.get("llm") is None
    assert json.loads((tmp_path / "state.json").read_text())["merge_offen"] == {}


def test_merge_retry_gives_up_and_flags_stephan(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(r, "POST", True)
    (tmp_path / "state.json").write_text(json.dumps(
        {"merge_offen": {"7": {"sha": SHA, "versuche": r.MERGE_RETRIES - 1}}, "erledigt": [f"pr7@{SHA}"]}))
    calls = _github_with_merge(monkeypatch, pr_view={"state": "OPEN", "headRefOid": SHA, "mergeStateStatus": "BLOCKED"})
    r.run_once()
    assert calls["merged"] == [] and calls["edits"] == [("7", "--add-label", "braucht-stephan")]
    assert calls["comments"] == ["7"] and "⚠ Stephan" in capsys.readouterr().out
    assert json.loads((tmp_path / "state.json").read_text())["merge_offen"] == {}


def test_merge_retry_drops_pr_with_new_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "POST", True)
    (tmp_path / "state.json").write_text(json.dumps({"merge_offen": {"7": {"sha": "b" * 40, "versuche": 0}}}))
    calls = _github_with_merge(monkeypatch, prs=[],
                               pr_view={"state": "OPEN", "headRefOid": SHA, "mergeStateStatus": "CLEAN"})
    r.run_once()
    assert calls["merged"] == []
    assert json.loads((tmp_path / "state.json").read_text())["merge_offen"] == {}


def test_manual_mode_flags_stephan_once(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "POST", True)
    calls = _github_with_merge(monkeypatch, merge_mode="manuell durch Stephan (Betrieb)")
    r.run_once(); r.run_once()
    assert calls["merged"] == [] and calls["comments"] == ["7"]
    assert calls["edits"] == [("7", "--add-label", "braucht-stephan")]


def test_flag_creates_missing_label(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "POST", True)
    tried = []

    def gh_text(*args, **kw):
        tried.append(args[:2])
        if args[:2] == ("pr", "edit") and ("label", "create") not in tried:
            raise r.subprocess.CalledProcessError(1, "gh", stderr="label not found")
        return ""
    monkeypatch.setattr(r, "gh_text", gh_text)
    r.to_stephan(7, "k", "Grund", {})
    assert tried == [("pr", "edit"), ("label", "create"), ("pr", "edit"), ("pr", "comment")]


def test_flag_in_dry_run_only_logs(monkeypatch, capsys):
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    r.to_stephan(7, "k", "Grund", {})
    assert "⚠ Stephan: Grund" in capsys.readouterr().out


def test_dry_run_unblock_is_logged_once(monkeypatch, capsys):
    monkeypatch.setattr(r, "gh_json", lambda *a, **k: [{"number": 231, "body": "Wartet auf: #228"}]
                        if a[:2] == ("issue", "list") else {"state": "CLOSED"})
    r.unblock_dependent_issues(); r.unblock_dependent_issues()
    assert capsys.readouterr().out.count("Issue #231") == 1
