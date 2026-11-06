import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_kulanz", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_kulanz"] = r
_spec.loader.exec_module(r)

SHA = "a" * 40


def _utc(hhmmss):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return datetime(2026, 11, 5, h, m, s, tzinfo=timezone.utc)


def _run(name, started=None, result="SUCCESS"):
    c = {"name": name, "status": "COMPLETED", "conclusion": result}
    if started:
        c["startedAt"] = started
    return c


@pytest.fixture(autouse=True)
def _pflicht(monkeypatch):
    monkeypatch.setattr(r, "REQUIRED_CHECKS", ("tests", "acceptance-guard"))
    monkeypatch.setattr(r, "MISSING_CHECK_GRACE_S", 600)


NUR_STATUS = [{"context": "review", "state": "SUCCESS"}]


def test_letzter_commit_zeit():
    pr = {"commits": [{"committedDate": "2026-11-05T13:52:20Z"}, {"committedDate": "2026-11-05T14:01:49Z"},
                      {"committedDate": "kaputt"}, {}]}
    assert r.letzter_commit_zeit(pr) == _utc("14:01:49")
    assert r.letzter_commit_zeit({"commits": [{"committedDate": "2026-11-05T14:01:49"}]}) == _utc("14:01:49")
    assert r.letzter_commit_zeit({}) is None
    assert r.letzter_commit_zeit({"commits": []}) is None
    assert r.letzter_commit_zeit({"commits": [{"committedDate": None}]}) is None


def test_ohne_startzeit_zaehlt_der_commit():
    done, rows, red = r.checks_summary(NUR_STATUS, now=_utc("14:04:37"), seit=_utc("14:01:49"))
    assert done is False and red == []
    assert not any(ergebnis == "FEHLT" for _, ergebnis in rows)
    done, rows, red = r.checks_summary(NUR_STATUS, now=_utc("14:11:50"), seit=_utc("14:01:49"))
    assert red == ["tests", "acceptance-guard"]
    assert ("tests", "FEHLT") in rows and ("acceptance-guard", "FEHLT") in rows


def test_alte_startzeit_eines_frueheren_laufs_zaehlt_nicht_vor_dem_commit():
    rollup = [_run("merge-nach-freigabe", started="2026-11-05T13:50:00Z")]
    done, _, red = r.checks_summary(rollup, now=_utc("14:04:37"), seit=_utc("14:01:49"))
    assert done is False and red == [], "Beginn ist der spätere Wert: der Commit um 14:01:49"


def test_spaetere_startzeit_gewinnt_vor_dem_commit():
    rollup = [_run("acceptance-guard", started="2026-11-05T14:05:00Z")]
    done, _, red = r.checks_summary(rollup, now=_utc("14:14:59"), seit=_utc("14:01:49"))
    assert done is False and red == []
    assert r.checks_summary(rollup, now=_utc("14:15:01"), seit=_utc("14:01:49"))[2] == ["tests"]


def test_naive_seit_gilt_als_utc():
    naiv = datetime(2026, 11, 5, 14, 1, 49)
    assert r.checks_summary(NUR_STATUS, now=_utc("14:04:37"), seit=naiv)[2] == []


def test_ohne_seit_wie_bisher():
    done, rows, red = r.checks_summary(NUR_STATUS, now=_utc("14:04:37"))
    assert done is True and red == ["tests", "acceptance-guard"]


# ---- Verdrahtung: kein Review, solange die Checks des frischen Commits noch nicht da sind ----

def test_review_ablauf_wartet_auf_checks_eines_frischen_commits(monkeypatch, tmp_path, capsys):
    p = r.projects.default_projects()[0]
    for name, wert in {"PROJECTS": [p], "REPO": p.repo, "REVIEW_SKILL": p.review_skill,
                       "PROJECT_AUTO_MERGE": True, "PROJECT_MANUAL_PATHS": (),
                       "BASE_STATE": str(tmp_path / "state.json"), "BASE_DRAFT_DIR": str(tmp_path / "reviews"),
                       "STATE": str(tmp_path / "state.json"), "DRAFT_DIR": str(tmp_path / "reviews"),
                       "REVIEW_KEY": "test-key", "REVIEWER_NAME": "GLM", "REVIEW_MODEL": "coder-glm",
                       "REVIEW_MODEL_BIG": "coder-glm", "POST": True, "AUTO_MERGE": True}.items():
        monkeypatch.setattr(r, name, wert)

    def no_network(*a, **k):
        raise r.urllib.error.URLError("kein Netz im Test")

    monkeypatch.setattr(r.urllib.request, "urlopen", no_network)
    monkeypatch.setattr(r, "_now", lambda: _utc("14:04:37"))
    pr = {"number": 7, "title": "Titel", "body": "Closes #5\n\nWorker: DeepSeek (LiteLLM coder-go, OpenCode Go)\n",
          "headRefName": "feature/issue-5", "headRefOid": SHA, "isCrossRepository": False, "isDraft": False,
          "labels": [], "reviews": [], "commits": [{"committedDate": "2026-11-05T14:01:49Z"}],
          "statusCheckRollup": NUR_STATUS}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("issue", "list"):
            return []
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "Issue", "body": "### Merge-Modus\n\nautomatisch nach Review\n"}
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "docs/a.md", "additions": 1, "deletions": 0}]}
        raise AssertionError(args)

    gepostet = []
    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/docs/a.md b/docs/a.md\n+x\n")
    monkeypatch.setattr(r, "call_llm", lambda model, messages: (_ for _ in ()).throw(AssertionError("kein LLM")))
    monkeypatch.setattr(r, "post_review", lambda n, t: gepostet.append(n))
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: gepostet.append(("status", verdict)) or True)
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: (_ for _ in ()).throw(AssertionError("kein Merge")))
    r.run_once()
    assert gepostet == [], "kein Review und kein Status, solange die Kulanzzeit läuft"
    assert "PR #7: Checks laufen noch, warte." in capsys.readouterr().out
