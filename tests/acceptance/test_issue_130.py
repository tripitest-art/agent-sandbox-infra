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


def _run(name, result, started=None, status="COMPLETED"):
    c = {"name": name, "status": status, "conclusion": result}
    if started:
        c["startedAt"] = started
    return c


GRUEN = [_run("tests", "SUCCESS", "2026-10-16T13:29:10Z"), _run("acceptance-guard", "SUCCESS", "2026-10-16T13:29:12Z")]


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


def _pr(number, worker=True, checks=GRUEN, **kw):
    body = "Was und warum\n\n" + ("Worker: Claude Sonnet 5.5\n" if worker else "")
    pr = {"number": number, "title": f"PR {number}", "body": body, "headRefName": f"feature/manuell-{number}",
          "headRefOid": SHA, "isCrossRepository": False, "isDraft": False, "labels": [{"name": LABEL}],
          "reviews": [], "commits": [{"committedDate": "2026-10-16T10:00:00Z"}], "statusCheckRollup": checks}
    pr.update(kw)
    return pr


def test_checks_summary_zaehlt_je_name_nur_den_neuesten_lauf():
    # Ein per Nebenläufigkeitsregel abgebrochener Lauf bleibt im Rollup stehen, der neuere ist grün.
    rollup = [_run("tests", "SUCCESS", "2026-10-16T13:29:10Z"),
              _run("acceptance-guard", "CANCELLED", "2026-10-16T13:29:12Z"),
              _run("acceptance-guard", "SUCCESS", "2026-10-16T13:29:40Z")]
    done, rows, red = r.checks_summary(rollup)
    assert done is True and red == []
    assert sorted(rows) == [("acceptance-guard", "SUCCESS"), ("tests", "SUCCESS")]


def test_checks_summary_reihenfolge_im_rollup_ist_egal():
    rollup = [_run("acceptance-guard", "SUCCESS", "2026-10-16T13:29:40Z"),
              _run("acceptance-guard", "CANCELLED", "2026-10-16T13:29:12Z"),
              _run("tests", "SUCCESS", "2026-10-16T13:29:10Z")]
    assert r.checks_summary(rollup)[2] == []


def test_checks_summary_ein_neuerer_roter_lauf_bleibt_rot():
    rollup = [_run("tests", "SUCCESS", "2026-10-16T13:29:10Z"),
              _run("acceptance-guard", "SUCCESS", "2026-10-16T13:29:12Z"),
              _run("acceptance-guard", "FAILURE", "2026-10-16T13:30:00Z")]
    done, rows, red = r.checks_summary(rollup)
    assert done is True and red == ["acceptance-guard"]


def test_checks_summary_ein_neuerer_laufender_check_ist_nicht_fertig():
    rollup = [_run("tests", "SUCCESS", "2026-10-16T13:29:10Z"),
              _run("acceptance-guard", "SUCCESS", "2026-10-16T13:29:12Z"),
              {"name": "acceptance-guard", "status": "IN_PROGRESS", "conclusion": "", "startedAt": "2026-10-16T13:31:00Z"}]
    assert r.checks_summary(rollup)[0] is False


def test_checks_summary_ohne_zeitstempel_gewinnt_der_spaetere_eintrag():
    rollup = [_run("tests", "SUCCESS"), _run("acceptance-guard", "CANCELLED"), _run("acceptance-guard", "SUCCESS")]
    assert r.checks_summary(rollup)[2] == []


def test_pick_pr_ueberspringt_die_nummern_aus_skip():
    prs = [_pr(7), _pr(8)]
    assert r.pick_pr(prs, {}, lambda n: [])["number"] == 7
    assert r.pick_pr(prs, {}, lambda n: [], skip=frozenset({7}))["number"] == 8
    assert r.pick_pr(prs, {}, lambda n: [], skip=frozenset({7, 8})) is None


def _takt(monkeypatch, prs):
    seen = {"posted": []}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return prs
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 1, "deletions": 0}]}
        if args[:2] == ("issue", "list"):
            return []
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/a.py b/a.py\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", lambda model, messages: (OK_REVIEW, {"prompt_tokens": 1, "completion_tokens": 1}))
    monkeypatch.setattr(r, "post_review", lambda n, t: seen["posted"].append(n))
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: True)
    monkeypatch.setattr(r, "to_stephan", lambda n, key, reason, state: None)
    return seen


def test_pr_ohne_worker_zeile_blockiert_die_juengeren_nicht(monkeypatch, capsys):
    seen = _takt(monkeypatch, [_pr(7, worker=False), _pr(8)])
    assert r.run_once() == 0
    assert seen["posted"] == [8]                       # schon im ersten Takt
    assert capsys.readouterr().out.count("PR #7: Zeile „Worker: …“ fehlt im PR-Text, kein Review") == 1


def test_die_meldung_zur_fehlenden_worker_zeile_kommt_nur_einmal_je_commit(monkeypatch, capsys):
    seen = _takt(monkeypatch, [_pr(7, worker=False), _pr(8)])
    assert r.run_once() == 0 and r.run_once() == 0 and r.run_once() == 0
    assert seen["posted"] == [8]
    assert capsys.readouterr().out.count("PR #7: Zeile „Worker: …“ fehlt im PR-Text") == 1


def test_ergaenzte_worker_zeile_wird_beim_naechsten_takt_geprueft(monkeypatch):
    prs = [_pr(7, worker=False)]
    seen = _takt(monkeypatch, prs)
    assert r.run_once() == 0 and seen["posted"] == []
    prs[0]["body"] += "Worker: Claude Sonnet 5.5\n"
    assert r.run_once() == 0
    assert seen["posted"] == [7]


def test_laufende_checks_eines_aelteren_prs_blockieren_die_juengeren_nicht(monkeypatch, capsys):
    laufend = [_run("tests", "SUCCESS", "2026-10-16T13:29:10Z"),
               {"name": "acceptance-guard", "status": "IN_PROGRESS", "conclusion": "", "startedAt": "2026-10-16T13:29:12Z"}]
    seen = _takt(monkeypatch, [_pr(7, checks=laufend), _pr(8)])
    assert r.run_once() == 0
    assert seen["posted"] == [8]
    assert "PR #7: Checks laufen noch, warte." in capsys.readouterr().out
