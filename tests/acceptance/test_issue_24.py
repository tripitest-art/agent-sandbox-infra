import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))

import ha_status  # noqa: E402
import projects  # noqa: E402

SECRET_URL = "http://ha.example:8123/api/webhook/GEHEIM123"


def _p(name="a", repo="o/a"):
    return projects.Project(name=name, repo=repo, workdir=f"/root/{name}")


def _issue(n, *labels):
    return {"number": n, "title": f"Issue {n}", "labels": [{"name": x} for x in ("worker-task", *labels)]}


def _pr(n, labels=(), checks=()):
    return {
        "number": n, "title": f"PR {n}", "url": f"https://github.com/o/a/pull/{n}", "isDraft": False,
        "labels": [{"name": x} for x in labels],
        "statusCheckRollup": [{"name": c, "status": s, "conclusion": r} for c, s, r in checks],
    }


GREEN = (("tests", "COMPLETED", "SUCCESS"), ("acceptance-guard", "COMPLETED", "SUCCESS"))
RED = (("tests", "COMPLETED", "FAILURE"), ("acceptance-guard", "COMPLETED", "SUCCESS"))
RUNNING = (("tests", "IN_PROGRESS", ""), ("acceptance-guard", "COMPLETED", "SUCCESS"))


def test_issue_counts_by_state_label():
    issues = [_issue(1, "bereit"), _issue(2, "bereit"), _issue(3, "in-arbeit"),
              _issue(4, "blockiert"), _issue(5, "entwurf"), _issue(6)]
    out = ha_status.summarize_project(_p(), issues, [])
    assert out["name"] == "a" and out["repo"] == "o/a"
    assert out["issues"] == {"bereit": 2, "in-arbeit": 1, "blockiert": 1, "entwurf": 1}


def test_pr_counts_open_red_running():
    prs = [_pr(10, checks=GREEN), _pr(11, checks=RED), _pr(12, checks=RUNNING), _pr(13, checks=RED + RUNNING)]
    out = ha_status.summarize_project(_p(), [], prs)["prs"]
    assert out["offen"] == 4
    assert out["rot"] == 2          # rot gewinnt vor laufend
    assert out["laufend"] == 1


def test_waiting_for_stephan_lists_labeled_prs_sorted_by_number():
    prs = [_pr(30, labels=["braucht-stephan"]), _pr(12), _pr(21, labels=["x", "braucht-stephan"])]
    waiting = ha_status.summarize_project(_p(), [], prs)["prs"]["wartet_auf_stephan"]
    assert waiting == [
        {"number": 21, "title": "PR 21", "url": "https://github.com/o/a/pull/21"},
        {"number": 30, "title": "PR 30", "url": "https://github.com/o/a/pull/30"},
    ]


def test_budget_summary():
    assert ha_status.summarize_budget({"spend": 0.9918452560000002, "max_budget": 5.0}) == \
        {"spend": 0.99, "max": 5.0, "prozent": 20}
    assert ha_status.summarize_budget({"spend": 0.0, "max_budget": None}) == \
        {"spend": 0.0, "max": None, "prozent": None}
    assert ha_status.summarize_budget(None) == {"fehler": "nicht erreichbar"}


def test_build_status_collects_projects_budgets_and_total():
    data = {
        ("issues", "o/a"): [_issue(1, "bereit")],
        ("prs", "o/a"): [_pr(5, labels=["braucht-stephan"], checks=GREEN)],
        ("issues", "o/b"): [],
        ("prs", "o/b"): [_pr(7, labels=["braucht-stephan"]), _pr(8)],
    }
    now = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)
    out = ha_status.build_status(
        [_p("a", "o/a"), _p("b", "o/b")], lambda kind, repo: data[(kind, repo)],
        {"worker": {"spend": 0.99, "max_budget": 5.0}, "reviewer": None}, now)
    assert out["zeit"] == "2026-10-14T16:00:00+00:00"
    assert [p["name"] for p in out["projekte"]] == ["a", "b"]
    assert out["projekte"][0]["issues"]["bereit"] == 1
    assert out["braucht_stephan"] == 2
    assert out["budgets"]["worker"] == {"spend": 0.99, "max": 5.0, "prozent": 20}
    assert out["budgets"]["reviewer"] == {"fehler": "nicht erreichbar"}
    json.dumps(out)                 # muss serialisierbar sein


def test_fetch_failure_marks_only_that_project_and_leaks_no_message():
    def fetch(kind, repo):
        if repo == "o/b":
            raise RuntimeError("boom token=abc123")
        return []
    now = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)
    out = ha_status.build_status([_p("a", "o/a"), _p("b", "o/b")], fetch, {}, now)
    assert out["projekte"][0]["name"] == "a" and "fehler" not in out["projekte"][0]
    assert out["projekte"][1] == {"name": "b", "repo": "o/b", "fehler": "RuntimeError"}
    assert "abc123" not in json.dumps(out)


class _Resp:
    def __init__(self, status):
        self.status = status

    def close(self):
        pass


def test_post_status_sends_json_post_and_reports_success():
    seen = {}

    def opener(req, timeout=None):
        seen["req"], seen["timeout"] = req, timeout
        return _Resp(200)

    payload = {"zeit": "x", "projekte": []}
    assert ha_status.post_status(SECRET_URL, payload, opener=opener, timeout=7) is True
    req = seen["req"]
    assert req.get_method() == "POST" and req.full_url == SECRET_URL
    assert json.loads(req.data.decode("utf-8")) == payload
    assert req.get_header("Content-type") == "application/json"
    assert seen["timeout"] == 7


def test_post_status_failure_returns_false_and_never_prints_the_url(capsys):
    def boom(req, timeout=None):
        raise OSError(f"kein Weg zu {SECRET_URL}")

    assert ha_status.post_status(SECRET_URL, {}, opener=boom) is False
    assert ha_status.post_status(SECRET_URL, {}, opener=lambda req, timeout=None: _Resp(500)) is False
    captured = capsys.readouterr()
    assert "GEHEIM123" not in captured.out + captured.err
