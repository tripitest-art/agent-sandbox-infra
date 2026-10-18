import importlib.util
import sys
import urllib.error
from datetime import datetime, timezone
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


def _utc(hhmmss: str) -> datetime:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return datetime(2026, 10, 17, h, m, s, tzinfo=timezone.utc)


def _run(name, result="SUCCESS", started="2026-10-17T08:00:00Z", status="COMPLETED"):
    c = {"name": name, "status": status, "conclusion": result}
    if started:
        c["startedAt"] = started
    return c


# acceptance-guard und wake starten sofort, tests (anderes Ereignis) ist noch nicht im Rollup
OHNE_TESTS = [_run("acceptance-guard", started="2026-10-17T08:00:00Z"), _run("wake", started="2026-10-17T08:00:01Z")]
ALLE = OHNE_TESTS + [_run("tests", started="2026-10-17T08:00:05Z")]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    p = r.projects.default_projects()[0]
    monkeypatch.setattr(r, "PROJECTS", [p])
    monkeypatch.setattr(r, "REPO", p.repo)
    monkeypatch.setattr(r, "REVIEW_SKILL", p.review_skill)
    monkeypatch.setattr(r, "PROJECT_AUTO_MERGE", False)
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


def test_fehlender_pflicht_check_gilt_in_der_kulanzzeit_als_laufend():
    done, rows, red = r.checks_summary(OHNE_TESTS, now=_utc("08:00:30"))
    assert done is False and red == []


def test_fehlender_pflicht_check_ist_nach_der_kulanzzeit_rot():
    done, rows, red = r.checks_summary(OHNE_TESTS, now=_utc("08:20:00"))
    assert done is True and red == ["tests"]
    assert ("tests", "FEHLT") in rows


def test_ohne_zeitstempel_gibt_es_keine_kulanzzeit():
    rollup = [_run("acceptance-guard", started=None), _run("wake", started=None)]
    done, rows, red = r.checks_summary(rollup, now=_utc("08:00:30"))
    assert done is True and red == ["tests"]


def test_sind_alle_pflicht_checks_da_aendert_sich_nichts():
    done, rows, red = r.checks_summary(ALLE, now=_utc("08:00:30"))
    assert done is True and red == []
    assert sorted(n for n, _ in rows) == ["acceptance-guard", "tests", "wake"]


def test_die_kulanzzeit_betraegt_zehn_minuten():
    assert r.MISSING_CHECK_GRACE_S == 600
    assert r.checks_summary(OHNE_TESTS, now=_utc("08:09:59"))[0] is False
    assert r.checks_summary(OHNE_TESTS, now=_utc("08:10:01"))[2] == ["tests"]


def _pr(checks):
    return {"number": 7, "title": "PR 7", "body": "Was und warum\n\nWorker: DeepSeek (LiteLLM coder)\n",
            "headRefName": "feature/manuell-7", "headRefOid": SHA, "isCrossRepository": False, "isDraft": False,
            "labels": [{"name": LABEL}], "reviews": [], "commits": [{"committedDate": "2026-10-17T07:00:00Z"}],
            "statusCheckRollup": checks}


def _takt(monkeypatch, checks, now):
    seen = {"calls": [], "posted": [], "checks": checks}
    monkeypatch.setattr(r, "_now", lambda: _utc(now), raising=False)

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [_pr(seen["checks"])]
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 1, "deletions": 0}]}
        if args[:2] == ("issue", "list"):
            return []
        raise AssertionError(args)

    def call_llm(model, messages):
        seen["calls"].append(model)
        return OK_REVIEW, {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "stop"}

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/a.py b/a.py\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", call_llm)
    monkeypatch.setattr(r, "post_review", lambda n, t: seen["posted"].append(t))
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: True)
    monkeypatch.setattr(r, "to_stephan", lambda n, key, reason, state: None)
    return seen


def test_in_der_kulanzzeit_wartet_der_takt_ohne_modellaufruf(monkeypatch, capsys):
    seen = _takt(monkeypatch, OHNE_TESTS, "08:00:30")
    assert r.run_once() == 0
    assert seen["calls"] == [] and seen["posted"] == []
    assert "PR #7: Checks laufen noch, warte." in capsys.readouterr().out


def test_nach_der_kulanzzeit_kommt_ein_review_ohne_modellaufruf(monkeypatch):
    seen = _takt(monkeypatch, OHNE_TESTS, "08:20:00")
    assert r.run_once() == 0
    assert seen["calls"] == [] and len(seen["posted"]) == 1
    text = seen["posted"][0]
    assert text.startswith("**Änderungen nötig**") and "`tests`" in text


def test_taucht_der_check_auf_wird_normal_geprueft(monkeypatch):
    seen = _takt(monkeypatch, OHNE_TESTS, "08:00:30")
    assert r.run_once() == 0 and seen["posted"] == []
    seen["checks"] = ALLE
    monkeypatch.setattr(r, "_now", lambda: _utc("08:02:30"))
    assert r.run_once() == 0
    assert seen["calls"] == ["coder-glm"] and len(seen["posted"]) == 1
    assert seen["posted"][0].startswith("**Freigegeben**")
