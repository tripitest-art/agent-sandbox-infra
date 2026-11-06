import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_neustart", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_neustart"] = r
_spec.loader.exec_module(r)

SHA = "a" * 40
KEY = f"pr7@{SHA}"
URL = "https://github.com/tripitest-art/sc-digger/actions/runs/{}/job/99"


def _run(name, conclusion, started, run_id=None):
    c = {"name": name, "status": "COMPLETED", "conclusion": conclusion, "startedAt": started}
    if run_id:
        c["detailsUrl"] = URL.format(run_id)
    return c


ABGEBROCHEN = [_run("tests", "CANCELLED", "2026-11-05T15:07:41Z", "42051776176"),
               _run("acceptance-guard", "SUCCESS", "2026-11-05T15:07:40Z", "42051776100")]


@pytest.fixture
def gh(monkeypatch):
    seen = {"text": [], "logs": [], "audit": [], "saved": 0}
    monkeypatch.setattr(r, "REQUIRED_CHECKS", ("tests", "acceptance-guard"))
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: seen["text"].append(list(a)) or "")
    monkeypatch.setattr(r, "log", seen["logs"].append)
    monkeypatch.setattr(r, "audit", lambda event, **f: seen["audit"].append((event, f)))
    monkeypatch.setattr(r, "save_state", lambda state: seen.__setitem__("saved", seen["saved"] + 1))
    return seen


def test_abgebrochene_pflichtchecks(gh):
    rollup = ABGEBROCHEN + [_run("CodeQL", "CANCELLED", "2026-11-05T15:07:42Z", "1"),
                            _run("acceptance-guard", "cancelled", "2026-11-05T15:00:00Z", "2")]
    assert r.abgebrochene_pflichtchecks(rollup) == {"tests": "42051776176"}
    neuer_gruen = ABGEBROCHEN + [_run("tests", "SUCCESS", "2026-11-05T18:40:00Z", "42051776176")]
    assert r.abgebrochene_pflichtchecks(neuer_gruen) == {}
    ohne_url = [_run("tests", "CANCELLED", "2026-11-05T15:07:41Z")]
    assert r.abgebrochene_pflichtchecks(ohne_url) == {}
    assert r.abgebrochene_pflichtchecks([]) == {}


def test_startet_einmal_neu_und_wartet(gh):
    state = {}
    assert r.abgebrochene_neu_starten(7, KEY, ["tests"], ABGEBROCHEN, state) is True
    assert gh["text"] == [["run", "rerun", "42051776176", "--failed"]]
    assert state[r.NEUSTART_KEY] == [KEY] and gh["saved"] >= 1
    assert "PR #7: Pflicht-Check tests abgebrochen, Lauf einmal neu gestartet; warte." in gh["logs"]
    assert ("checks_neu_gestartet", {"pr": 7, "checks": ["tests"]}) in gh["audit"]
    assert r.abgebrochene_neu_starten(7, KEY, ["tests"], ABGEBROCHEN, state) is False, "je Commit nur einmal"
    assert len(gh["text"]) == 1


def test_echter_fehler_wird_nicht_neu_gestartet(gh):
    rollup = ABGEBROCHEN + [_run("acceptance-guard", "FAILURE", "2026-11-05T15:08:00Z", "5")]
    assert r.abgebrochene_neu_starten(7, KEY, ["tests", "acceptance-guard"], rollup, {}) is False
    assert r.abgebrochene_neu_starten(7, KEY, [], ABGEBROCHEN, {}) is False
    assert gh["text"] == []


def test_neustart_scheitert_faellt_auf_das_bisherige_zurueck(monkeypatch, gh):
    def kaputt(*a, **k):
        raise subprocess.CalledProcessError(1, ["gh"], output="", stderr="HTTP 403: Resource not accessible")

    monkeypatch.setattr(r, "gh_text", kaputt)
    state = {}
    assert r.abgebrochene_neu_starten(7, KEY, ["tests"], ABGEBROCHEN, state) is False
    assert state[r.NEUSTART_KEY] == [KEY], "kein zweiter Versuch im nächsten Takt"
    assert any(z.startswith("PR #7: Neustart des abgebrochenen Laufs 42051776176 fehlgeschlagen (") for z in gh["logs"])


def test_review_ablauf_postet_nichts_beim_ersten_abbruch(monkeypatch, tmp_path):
    p = r.projects.default_projects()[0]
    for name, wert in {"PROJECTS": [p], "REPO": p.repo, "REVIEW_SKILL": p.review_skill,
                       "PROJECT_AUTO_MERGE": True, "PROJECT_MANUAL_PATHS": (),
                       "REQUIRED_CHECKS": ("tests", "acceptance-guard"),
                       "BASE_STATE": str(tmp_path / "state.json"), "BASE_DRAFT_DIR": str(tmp_path / "reviews"),
                       "STATE": str(tmp_path / "state.json"), "DRAFT_DIR": str(tmp_path / "reviews"),
                       "REVIEW_KEY": "test-key", "REVIEWER_NAME": "GLM", "REVIEW_MODEL": "coder-glm",
                       "REVIEW_MODEL_BIG": "coder-glm", "POST": True, "AUTO_MERGE": True}.items():
        monkeypatch.setattr(r, name, wert)

    def no_network(*a, **k):
        raise r.urllib.error.URLError("kein Netz im Test")

    monkeypatch.setattr(r.urllib.request, "urlopen", no_network)
    pr = {"number": 7, "title": "Titel", "body": "Closes #5\n\nWorker: DeepSeek (LiteLLM coder-go, OpenCode Go)\n",
          "headRefName": "feature/issue-5", "headRefOid": SHA, "isCrossRepository": False, "isDraft": False,
          "labels": [], "reviews": [], "commits": [{"committedDate": "2026-11-05T15:07:30Z"}],
          "statusCheckRollup": ABGEBROCHEN}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("issue", "list"):
            return []
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "Issue", "body": "### Merge-Modus\n\nautomatisch nach Review\n"}
        raise AssertionError(args)

    gh_aufrufe, gepostet = [], []
    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: gh_aufrufe.append(list(a)) or "")
    monkeypatch.setattr(r, "call_llm", lambda model, messages: (_ for _ in ()).throw(AssertionError("kein LLM")))
    monkeypatch.setattr(r, "post_review", lambda n, t: gepostet.append(n))
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: gepostet.append(("status", verdict)) or True)
    assert r.run_once() == 0
    assert gepostet == []
    assert ["run", "rerun", "42051776176", "--failed"] in gh_aufrufe
