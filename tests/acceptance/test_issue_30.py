"""Akzeptanztests für Issue <N>: Merge an den geprüften Commit, keine Draft-PRs."""
import importlib.util
import sys
from pathlib import Path


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


r = _load("review_tick", "sandbox/review_tick.py")

SHA = "a" * 40
GOOD_CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"}
               for n in ("tests", "acceptance-guard")]


def _pr(number=7, **kw):
    pr = {"number": number, "title": "Titel", "body": "Closes #5\n\nWorker: DeepSeek (LiteLLM coder-fast)\n",
          "headRefName": f"feature/issue-{number}", "headRefOid": SHA, "isCrossRepository": False,
          "isDraft": False, "reviews": [], "commits": [{"committedDate": "2026-10-02T10:00:00Z"}],
          "statusCheckRollup": GOOD_CHECKS}
    pr.update(kw)
    return pr


def _collect_gh(monkeypatch):
    """Fängt die argv-Listen ab, die an `gh` gehen; Log und Zustand bleiben stumm."""
    seen = []
    monkeypatch.setattr(r, "gh_text", lambda *args, **kw: seen.append(list(args)) or "")
    monkeypatch.setattr(r, "save_state", lambda state: None)
    monkeypatch.setattr(r, "log", lambda msg: None)
    return seen


def test_merge_after_review_pins_the_reviewed_commit(monkeypatch):
    seen = _collect_gh(monkeypatch)
    assert r.merge_or_queue(7, SHA, {}) is True
    assert seen == [["pr", "merge", "7", "--squash", "--match-head-commit", SHA]]


def test_retry_merges_pins_the_queued_commit(monkeypatch):
    seen = _collect_gh(monkeypatch)
    monkeypatch.setattr(r, "gh_json", lambda *a, **kw: {"state": "OPEN", "headRefOid": SHA,
                                                       "mergeStateStatus": "CLEAN"})
    state = {"merge_offen": {"7": {"sha": SHA, "versuche": 0}}}
    r.retry_merges(state)
    assert seen == [["pr", "merge", "7", "--squash", "--match-head-commit", SHA]]
    assert state["merge_offen"] == {}


def test_pick_pr_skips_draft_prs():
    labels = {5: ["agent-qwen"], 6: ["agent-qwen"], 8: ["agent-qwen"]}
    prs = [_pr(5, isDraft=True), _pr(6)]
    got = r.pick_pr(prs, {}, lambda n: labels[n])
    assert got and got["number"] == 6
    assert r.pick_pr([_pr(8, isDraft=True)], {}, lambda n: labels[n]) is None


def test_pr_list_requests_the_draft_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "coder-glm")
    monkeypatch.setattr(r, "REVIEWER_NAME", "GLM")
    monkeypatch.setattr(r, "load_state", lambda: {})

    def fake_json(*args):
        seen.append(args)
        return []

    monkeypatch.setattr(r, "gh_json", fake_json)
    r._run_pr_review()
    calls = [c for c in seen if c[:2] == ("pr", "list")]
    assert calls and any("isDraft" in arg for arg in calls[0])
