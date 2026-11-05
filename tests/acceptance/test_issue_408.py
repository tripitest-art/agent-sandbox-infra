import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_reviewer_app", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_reviewer_app"] = w
_spec.loader.exec_module(w)

AENDERUNGEN = "**Änderungen nötig**\n\n### Muss\n- `f` prüft None nicht (sandbox/a.py)."


class FakeGH:
    def __init__(self, prs):
        self.prs = list(prs)

    def __call__(self, *args, **kwargs):
        if args[:2] == ("pr", "list"):
            return self.prs
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": w.LABEL}], "title": "Titel"}
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _pr(login, body=AENDERUNGEN):
    return {"number": 103, "headRefName": "feature/issue-100", "headRefOid": "3373e35",
            "isCrossRepository": False, "commits": [{"committedDate": "2026-09-29T21:37:00Z"}],
            "reviews": [{"submittedAt": "2026-09-29T21:39:23Z", "body": body,
                         "author": {"login": login}, "commit": {"oid": "3373e35"}}],
            "title": "T", "body": "x"}


def test_review_authors_enthaelt_owner_und_genau_die_reviewer_app():
    assert w.OWNER in w.REVIEW_AUTHORS
    assert "sandbox-agent-reviewer-app" in w.REVIEW_AUTHORS
    assert "sandbox-agent-reviewer-app[bot]" in w.REVIEW_AUTHORS
    assert "app/sandbox-agent-reviewer-app" in w.REVIEW_AUTHORS
    assert len(w.REVIEW_AUTHORS) == 4


def test_review_author_ok_prueft_den_login():
    assert w.review_author_ok({"author": {"login": w.OWNER}})
    assert w.review_author_ok({"author": {"login": "sandbox-agent-reviewer-app[bot]"}})
    assert not w.review_author_ok({"author": {"login": "fremder"}})
    assert not w.review_author_ok({"author": {"login": "copilot-pull-request-reviewer[bot]"}})
    assert not w.review_author_ok({"author": {"login": "Sandbox-Agent-Reviewer-App"}})
    assert not w.review_author_ok({"author": None})
    assert not w.review_author_ok({"author": {}})
    assert not w.review_author_ok({})


def test_nacharbeit_aus_einem_review_der_reviewer_app(monkeypatch):
    for login in ("sandbox-agent-reviewer-app", "sandbox-agent-reviewer-app[bot]",
                  "app/sandbox-agent-reviewer-app"):
        monkeypatch.setattr(w, "gh", FakeGH([_pr(login)]))
        job = w.rework_job()
        assert job is not None, login
        key, task, pr_no, _ = job
        assert key == "pr103@2026-09-29T21:39:23Z"
        assert pr_no == 103
        assert AENDERUNGEN in task


def test_fremde_bots_loesen_keine_nacharbeit_aus(monkeypatch):
    for login in ("fremder", "copilot-pull-request-reviewer[bot]", "sandbox-agent-worker-tripitest[bot]"):
        monkeypatch.setattr(w, "gh", FakeGH([_pr(login)]))
        assert w.rework_job() is None, login


def test_owner_bleibt_zulaessig(monkeypatch):
    monkeypatch.setattr(w, "gh", FakeGH([_pr(w.OWNER)]))
    assert w.rework_job() is not None
