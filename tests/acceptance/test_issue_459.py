"""Akzeptanztests: hängende Fälle im Dashboard."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
_spec = importlib.util.spec_from_file_location("dashboard_haengt", _SANDBOX / "dashboard.py")
d = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_haengt"] = d
_spec.loader.exec_module(d)

NOW = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)


def _issue(n, labels, updated):
    return {"number": n, "title": f"Issue {n}", "labels": [{"name": x} for x in labels], "updatedAt": updated}


def _pr(n, branch, created, reviews=(), draft=False):
    return {"number": n, "title": f"PR {n}", "url": f"https://example.invalid/pull/{n}",
            "headRefName": branch, "createdAt": created, "reviews": list(reviews),
            "isDraft": draft, "labels": [], "statusCheckRollup": []}


def test_schwellen():
    assert d.STUCK_ISSUE_HOURS == 24
    assert d.STUCK_PR_HOURS == 48


def test_in_arbeit_ohne_pr():
    issues = [
        _issue(1, ["worker-task", "in-arbeit"], "2026-11-18T10:00:00Z"),   # 26 h, kein PR -> hängt
        _issue(2, ["worker-task", "in-arbeit"], "2026-11-19T02:00:00Z"),   # 10 h -> noch nicht
        _issue(3, ["worker-task", "in-arbeit"], "2026-11-17T12:00:00Z"),   # hat PR -> nicht
        _issue(4, ["worker-task", "bereit"], "2026-11-01T12:00:00Z"),      # nicht in Arbeit
        _issue(5, ["worker-task", "in-arbeit"], None),                     # ohne Zeit -> nicht
    ]
    prs = [_pr(30, "feature/issue-3-thema", "2026-11-19T11:00:00Z")]
    treffer = [e for e in d.find_stuck(issues, prs, NOW) if e["art"] == "in-arbeit-ohne-pr"]
    assert treffer == [{"art": "in-arbeit-ohne-pr", "number": 1, "title": "Issue 1", "seit_h": 26}]


def test_branch_muss_genau_passen():
    issues = [_issue(1, ["in-arbeit"], "2026-11-17T12:00:00Z")]
    prs = [_pr(30, "feature/issue-12-anderes", "2026-11-19T11:00:00Z")]
    assert [e["number"] for e in d.find_stuck(issues, prs, NOW) if e["art"] == "in-arbeit-ohne-pr"] == [1]


def test_pr_ohne_review():
    prs = [
        _pr(40, "feature/issue-4-a", "2026-11-16T12:00:00Z"),                          # 72 h -> hängt
        _pr(41, "feature/issue-5-b", "2026-11-16T12:00:00Z", reviews=[{"state": "COMMENTED"}]),
        _pr(42, "feature/issue-6-c", "2026-11-18T12:00:00Z"),                          # 24 h -> nicht
        _pr(43, "feature/issue-7-d", "2026-11-10T12:00:00Z", draft=True),              # Entwurf
    ]
    assert d.find_stuck([], prs, NOW) == [{
        "art": "pr-ohne-review", "number": 40, "title": "PR 40",
        "url": "https://example.invalid/pull/40", "seit_h": 72,
    }]


def test_naive_zeit_gilt_als_utc_und_unlesbares_zaehlt_nicht():
    naiv = datetime(2026, 11, 19, 12, 0, 0)
    prs = [_pr(40, "x", "2026-11-16T12:00:00Z"), _pr(41, "y", "kaputt")]
    assert [e["number"] for e in d.find_stuck([], prs, naiv)] == [40]


def test_summarize_project_traegt_die_liste():
    p = d.projects.Project(name="proj", repo="org/proj", workdir="/tmp/proj")
    issues = [_issue(1, ["in-arbeit"], "2026-11-17T12:00:00Z")]
    assert d.summarize_project(p, issues, [])["haengend"] == []
    mit = d.summarize_project(p, issues, [], now=NOW)
    assert [e["number"] for e in mit["haengend"]] == [1]


def test_build_status_und_seite():
    p = d.projects.Project(name="proj", repo="org/proj", workdir="/tmp/proj")
    def fetch(kind, repo):
        return [_issue(1, ["in-arbeit"], "2026-11-17T12:00:00Z")] if kind == "issues" else []
    res = d.build_status([p], fetch, {}, NOW)
    assert [e["number"] for e in res["projekte"][0]["haengend"]] == [1]
    assert '<h2>Hängt</h2><div id="haengend"></div>' in d.PAGE
    assert d.PAGE.index('id="stephan"') < d.PAGE.index('id="haengend"')
