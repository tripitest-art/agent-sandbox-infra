import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


projects = _load("projects")
r = _load("review_tick")

SHA = "a" * 40
OK_REVIEW = "**Freigegeben**\n\n### Muss\nkeine\n\n### Kann\n- Import nach oben\n\n### Geprüft\n- alles"
CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"} for n in ("tests", "acceptance-guard")]
ISSUE_AUTO = {"number": 5, "title": "Doku", "body": "### Ziel\n\nx\n\n### Merge-Modus\n\nautomatisch\n"}


def _projects_json(extra):
    entry = {"name": "p", "repo": "o/p", "workdir": "/w/p", **extra}
    return json.dumps({"projects": [entry]})


def test_projektliste_kennt_manual_paths():
    assert projects.parse_projects(_projects_json({}))[0].manual_paths == ()
    got = projects.parse_projects(_projects_json({"manual_paths": ["sandbox/*", "AGENTS.md"]}))[0]
    assert got.manual_paths == ("sandbox/*", "AGENTS.md")


@pytest.mark.parametrize("falsch", ["sandbox/*", [""], [1], {"a": "b"}])
def test_projektliste_lehnt_unbrauchbare_manual_paths_ab(falsch):
    with pytest.raises(projects.ProjectConfigError):
        projects.parse_projects(_projects_json({"manual_paths": falsch}))


def test_manual_path_hits_nennt_getroffene_dateien_in_der_reihenfolge_der_liste():
    files = ["docs/a.md", "sandbox/worker_tick.py", "AGENTS.md", "sandbox/systemd/x.service", "sandbox/worker_tick.py"]
    assert r.manual_path_hits(files, ("sandbox/*", "AGENTS.md")) == [
        "sandbox/worker_tick.py", "AGENTS.md", "sandbox/systemd/x.service"]
    assert r.manual_path_hits(files, ()) == []
    assert r.manual_path_hits(["docs/a.md", "README.md"], ("sandbox/*",)) == []


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    p = projects.default_projects()[0]
    monkeypatch.setattr(r, "PROJECTS", [p])
    monkeypatch.setattr(r, "REPO", p.repo)
    monkeypatch.setattr(r, "REVIEW_SKILL", p.review_skill)
    monkeypatch.setattr(r, "PROJECT_AUTO_MERGE", True)
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
    monkeypatch.setattr(r, "PROJECT_MANUAL_PATHS", (), raising=False)

    def no_network(*a, **k):
        raise urllib.error.URLError("kein Netz im Test")
    monkeypatch.setattr(r.urllib.request, "urlopen", no_network)


def _lauf(monkeypatch, files, manual_paths):
    """Ein Takt mit freigegebenem PR #7 (Issue: Merge-Modus automatisch); `files` ist die Dateiliste des PRs."""
    monkeypatch.setattr(r, "PROJECT_MANUAL_PATHS", manual_paths, raising=False)
    seen = {"merges": [], "reasons": []}
    pr = {"number": 7, "title": "Titel", "body": "Closes #5\n\nWorker: DeepSeek (LiteLLM coder-fast)\n",
          "headRefName": "feature/issue-5", "headRefOid": SHA, "isCrossRepository": False, "isDraft": False,
          "labels": [], "reviews": [], "commits": [{"committedDate": "2026-10-16T10:00:00Z"}],
          "statusCheckRollup": CHECKS}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return ISSUE_AUTO
        if args[:2] == ("pr", "view"):
            if files is None:
                raise r.subprocess.CalledProcessError(1, ["gh"], stderr="boom")
            return {"files": [{"path": f, "additions": 1, "deletions": 0} for f in files]}
        if args[:2] == ("issue", "list"):
            return []
        raise AssertionError(args)

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/x b/x\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", lambda model, messages: (OK_REVIEW, {"prompt_tokens": 1, "completion_tokens": 1}))
    monkeypatch.setattr(r, "post_review", lambda n, t: None)
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: True)
    monkeypatch.setattr(r, "merge_pr", lambda n, sha: seen["merges"].append(n) or True)
    monkeypatch.setattr(r, "to_stephan", lambda n, key, reason, state: seen["reasons"].append(reason))
    assert r.run_once() == 0
    return seen


def test_pr_ohne_geschuetzte_dateien_wird_automatisch_gemergt(monkeypatch):
    seen = _lauf(monkeypatch, ["docs/a.md", "CHANGELOG.md"], ("sandbox/*", "AGENTS.md"))
    assert seen["merges"] == [7] and seen["reasons"] == []


def test_pr_mit_geschuetzter_datei_geht_an_stephan(monkeypatch):
    seen = _lauf(monkeypatch, ["docs/a.md", "sandbox/worker_tick.py"], ("sandbox/*", "AGENTS.md"))
    assert seen["merges"] == []
    assert seen["reasons"] == ["Freigegeben, berührt Dateien mit manuellem Merge (sandbox/worker_tick.py): bitte prüfen und mergen."]


def test_unlesbare_dateiliste_sperrt_den_automatischen_merge(monkeypatch):
    seen = _lauf(monkeypatch, None, ("sandbox/*",))
    assert seen["merges"] == []
    assert seen["reasons"] == ["Freigegeben, Dateiliste nicht lesbar: bitte prüfen und mergen."]


def test_projekt_ohne_manual_paths_mergt_wie_bisher(monkeypatch):
    seen = _lauf(monkeypatch, ["sandbox/worker_tick.py"], ())
    assert seen["merges"] == [7] and seen["reasons"] == []
