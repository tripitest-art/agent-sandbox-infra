"""Tests für sandbox/projects.py (Projektliste) und das Umschalten in worker_tick und review_tick."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
sys.path.insert(0, str(_SANDBOX))
import projects as pj  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOOD = {"projects": [
    {"name": "sc-digger", "repo": "tripitest-art/sc-digger", "workdir": "/root/sc-digger",
     "hotspot": "sc_digger/main.py", "review_skill": "skills/sc-digger-review/SKILL.md"},
    {"name": "infra", "repo": "tripitest-art/agent-sandbox-infra", "workdir": "/root/agent-sandbox-infra",
     "hotspot": None, "auto_merge": False, "manual_paths": ["sandbox/*", "AGENTS.md"]},
]}


def _text(**changes):
    data = json.loads(json.dumps(GOOD))
    data["projects"][1].update(changes)
    return json.dumps(data)


# ---- Laden und Prüfen ----

def test_without_file_only_sc_digger(tmp_path):
    ps = pj.load_projects(str(tmp_path / "gibt-es-nicht.json"))
    assert [p.name for p in ps] == ["sc-digger"]
    assert ps[0].hotspot == "sc_digger/main.py" and ps[0].auto_merge is True


def test_path_from_environment(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(GOOD), encoding="utf-8")
    assert [p.name for p in pj.load_projects(env={"AGENT_PROJECTS": str(f)})] == ["sc-digger", "infra"]


def test_parse_defaults_and_values():
    a, b = pj.parse_projects(json.dumps(GOOD))
    assert a.test_cmd == "python -m pytest -q" and a.hotspot_label == "berührt-main.py"
    assert b.hotspot is None and b.review_skill is None and b.auto_merge is False
    assert a.required_checks == ("tests", "acceptance-guard") and b.required_checks == a.required_checks
    assert pj.worktrees_dir(b) == "/root/agent-sandbox-infra-worktrees"


def test_required_checks_from_project_list():
    """Jedes Projekt nennt seine Pflicht-Checks selbst (Issue #54); ohne Angabe gilt die Vorgabe."""
    a, b = pj.parse_projects(_text(required_checks=["tests", "acceptance-guard", "CodeQL"]))
    assert b.required_checks == ("tests", "acceptance-guard", "CodeQL")   # JSON-Liste wird zum Tupel
    assert a.required_checks == ("tests", "acceptance-guard")


def test_manual_paths_default_is_empty_and_list_becomes_tuple():
    """Dateimuster für den manuellen Merge (Issue #122): ohne Angabe leer, aus JSON wird ein Tupel."""
    a, b = pj.parse_projects(_text(manual_paths=["sandbox/*", "AGENTS.md"]))
    assert a.manual_paths == ()
    assert b.manual_paths == ("sandbox/*", "AGENTS.md")
    assert pj.parse_projects(json.dumps(GOOD))[1].manual_paths == ("sandbox/*", "AGENTS.md")


def test_project_without_manual_paths_is_allowed():
    """Ein Projekt ohne Muster ist erlaubt — nur ein unbrauchbares Muster wird abgewiesen."""
    assert pj.parse_projects(_text(manual_paths=[]))[1].manual_paths == ()


def test_null_test_cmd_falls_back_to_default():
    assert pj.parse_projects(_text(test_cmd=None))[1].test_cmd == "python -m pytest -q"


@pytest.mark.parametrize("changes, expected", [
    ({"name": "Infra Repo"}, "name"),
    ({"repo": "nur-ein-name"}, "repo"),
    ({"workdir": "relativ/pfad"}, "workdir"),
    ({"auto_merge": "nein"}, "auto_merge"),
    ({"required_checks": "tests"}, "required_checks"),
    ({"required_checks": []}, "required_checks"),
    ({"required_checks": ["tests", ""]}, "required_checks"),
    ({"required_checks": ["tests", 7]}, "required_checks"),
    ({"manual_paths": "sandbox/*"}, "manual_paths"),
    ({"manual_paths": [""]}, "manual_paths"),
    ({"manual_paths": [7]}, "manual_paths"),
    ({"manual_paths": {"a": "b"}}, "manual_paths"),
    ({"test_cmd": ""}, "test_cmd"),
    ({"unbekannt": 1}, "unbekannt"),
    ({"name": "sc-digger"}, "doppelt"),
])
def test_invalid_entry_is_rejected(changes, expected):
    with pytest.raises(pj.ProjectConfigError) as e:
        pj.parse_projects(_text(**changes))
    assert expected in str(e.value)


@pytest.mark.parametrize("text", ["", "kein json", "[]", '{"projects": []}', '{"projects": [1]}'])
def test_invalid_file_is_rejected(text):
    with pytest.raises(pj.ProjectConfigError):
        pj.parse_projects(text)


def test_broken_file_names_the_path(tmp_path):
    f = tmp_path / "p.json"
    f.write_text("{", encoding="utf-8")
    with pytest.raises(pj.ProjectConfigError) as e:
        pj.load_projects(str(f))
    assert str(f) in str(e.value)


def test_example_file_is_valid():
    text = (_SANDBOX / "projects.example.json").read_text(encoding="utf-8")
    ps = pj.parse_projects(text)
    assert [p.name for p in ps] == ["sc-digger", "agent-sandbox-infra"]
    assert ps[0].required_checks == ("tests", "acceptance-guard", "CodeQL")   # sc-digger verlangt CodeQL
    assert ps[1].required_checks == ("tests", "acceptance-guard")             # dieses Repo: Vorgabe
    # Das Beispiel zeigt die Aufsichtsdateien mit manuellem Merge (Issue #122); Skripte unter sandbox/ mergt der Takt.
    assert ps[1].auto_merge is True
    assert "AGENTS.md" in ps[1].manual_paths and "sandbox/*" not in ps[1].manual_paths


# ---- Ausgabe ----

def test_format_projects_automatic_merge_without_hotspot():
    p = pj.Project(name="infra", repo="o/infra", workdir="/root/infra", auto_merge=True, hotspot=None)
    assert pj.format_projects([p]) == "infra (o/infra): Merge automatisch, kein Hotspot"


# ---- Worker ----

@pytest.fixture
def w(monkeypatch):
    mod = _load("worker_tick")
    monkeypatch.setattr(mod, "PROJECTS", pj.parse_projects(json.dumps(GOOD)))
    monkeypatch.setattr(mod, "STATE", "/nonexistent/state.json")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod.os.path, "isdir", lambda p: True)
    return mod


def test_worker_single_project_does_not_switch(w, monkeypatch):
    monkeypatch.setattr(w, "PROJECTS", w.PROJECTS[:1])
    monkeypatch.setattr(w, "use_project", lambda *a: pytest.fail("darf nicht umschalten"))
    monkeypatch.setattr(w, "find_job", lambda state: ("issue1", "t", None, None))
    assert w.pick_job({})[0] == "issue1"


def test_worker_takes_first_project_with_work(w, monkeypatch):
    seen = []

    def fake_find(state):
        seen.append(w.REPO)
        return ("issue7", "t", None, None) if w.PROJECT == "infra" else None

    monkeypatch.setattr(w, "find_job", fake_find)
    assert w.pick_job({})[0] == "issue7"
    assert seen == ["tripitest-art/sc-digger", "tripitest-art/agent-sandbox-infra"]
    assert (w.PROJECT, w.PREFIX, w.BASE_WORKDIR) == ("infra", "infra/", "/root/agent-sandbox-infra")
    assert w.HOTSPOT is None


def test_worker_first_project_has_priority(w, monkeypatch):
    monkeypatch.setattr(w, "find_job", lambda state: (f"issue-{w.PROJECT}", "t", None, None))
    assert w.pick_job({})[0] == "issue-sc-digger"
    assert w.PREFIX == ""


def test_worker_skips_missing_clone_once(w, monkeypatch, capsys):
    monkeypatch.setattr(w.os.path, "isdir", lambda p: p != "/root/agent-sandbox-infra")
    monkeypatch.setattr(w, "find_job", lambda state: None)
    state = {}
    assert w.pick_job(state) is None
    assert w.pick_job(state) is None
    assert capsys.readouterr().out.count("Klon /root/agent-sandbox-infra fehlt") == 1


def test_worker_slot_worktree_per_project(w, monkeypatch):
    monkeypatch.setattr(w, "WORKER_SLOT", "2")
    w.use_project(w.PROJECTS[1], first=False)
    assert w.WORKDIR == "/root/agent-sandbox-infra-worktrees/w2"
    w.use_project(w.PROJECTS[0], first=True)
    assert w.WORKDIR == "/root/sc-digger-worktrees/w2"


def test_worker_done_keys_are_per_project(w):
    state = {w.DONE: ["pr5@x", "infra/pr5@y", "text9@abc"]}
    w.use_project(w.PROJECTS[0], first=True)
    assert w.project_done(state) == {"pr5@x", "text9@abc"}
    w.use_project(w.PROJECTS[1], first=False)
    assert w.project_done(state) == {"pr5@y"}


def test_worker_hotspot_is_per_project(w):
    w.use_project(w.PROJECTS[1], first=False)
    issue = {"labels": [{"name": "berührt-main.py"}], "body": "### Betroffene Dateien\n- `sc_digger/main.py`"}
    assert w.touches_main_py(issue) is False          # infra kennt keinen Hotspot
    w.use_project(w.PROJECTS[0], first=True)
    assert w.touches_main_py(issue) is True


def test_worker_prompt_uses_project_test_command(w, monkeypatch):
    infra = w.PROJECTS[1].__class__(**{**w.PROJECTS[1].__dict__, "test_cmd": "make test"})
    monkeypatch.setattr(w, "PROJECTS", [w.PROJECTS[0], infra])
    w.use_project(infra, first=False)
    issue = {"number": 3, "title": "T", "body": "### Betroffene Dateien\n- `a.py`", "labels": [],
             "author": {"login": "tripitest-art"}}

    def fake_gh(*args):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else [issue]
        return None

    monkeypatch.setattr(w, "gh", fake_gh)
    monkeypatch.setattr(w, "pr_template", lambda: "")   # Vorlage kommt nicht vom Rechner
    _, task, _, _ = w.issue_job()
    assert "`make test`" in task and "python -m pytest -q" not in task


# ---- Review-Takt ----

@pytest.fixture
def r(monkeypatch, tmp_path):
    mod = _load("review_tick")
    monkeypatch.setattr(mod, "PROJECTS", pj.parse_projects(json.dumps(GOOD)))
    monkeypatch.setattr(mod, "BASE_STATE", str(tmp_path / "review-state.json"))
    monkeypatch.setattr(mod, "BASE_DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(mod, "REVIEW_KEY", "k")
    # Auch diesen aus PROJECTS abgeleiteten Modulzustand festlegen: Sonst hinge das Ergebnis an der
    # Projektliste des Servers (/etc/agent-projects.json), die beim Import gelesen wurde.
    monkeypatch.setattr(mod, "PROJECT_MANUAL_PATHS", ())
    monkeypatch.setattr(mod, "unblock_dependent_issues", lambda: [])
    return mod


def test_review_paths_per_project(r, tmp_path):
    r.use_project(r.PROJECTS[1], first=False)
    assert r.STATE == str(tmp_path / "review-state-infra.json")
    assert r.DRAFT_DIR == str(tmp_path / "reviews-infra")
    assert (r.REPO, r.REVIEW_SKILL, r.PROJECT_AUTO_MERGE) == ("tripitest-art/agent-sandbox-infra", None, False)
    assert r.PROJECT_MANUAL_PATHS == ("sandbox/*", "AGENTS.md")   # Dateimuster je Projekt (Issue #122)
    r.use_project(r.PROJECTS[0], first=True)
    assert r.STATE == str(tmp_path / "review-state.json") and r.DRAFT_DIR == str(tmp_path / "reviews")
    assert r.PROJECT_AUTO_MERGE is True and r.PROJECT_MANUAL_PATHS == ()


def test_review_visits_every_project_and_isolates_errors(r, monkeypatch, capsys):
    visited = []

    def fake_review():
        visited.append(r.REPO)
        if r.REPO.endswith("sc-digger"):
            raise RuntimeError("kaputt")
        return 0

    monkeypatch.setattr(r, "_run_pr_review", fake_review)
    assert r.run_once() == 0
    assert visited == ["tripitest-art/sc-digger", "tripitest-art/agent-sandbox-infra"]
    assert "Projekt sc-digger: Fehler: RuntimeError" in capsys.readouterr().out


def test_review_never_merges_in_manual_project(r, monkeypatch, capsys):
    monkeypatch.setattr(r, "POST", True)
    monkeypatch.setattr(r, "AUTO_MERGE", True)
    monkeypatch.setattr(r, "PROJECTS", r.PROJECTS[1:])      # nur das Projekt mit auto_merge=false
    r.use_project(r.PROJECTS[0], first=True)
    r.STATE = r.BASE_STATE
    pr = {"number": 7, "title": "T", "body": "Closes #5\n\nWorker: DeepSeek (LiteLLM coder-fast)\n",
          "headRefName": "feature/issue-5", "headRefOid": "a" * 40, "isCrossRepository": False, "reviews": [],
          "commits": [{"committedDate": "2026-10-02T10:00:00Z"}],
          "statusCheckRollup": [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"}
                                for n in ("tests", "acceptance-guard", "CodeQL")]}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "T", "body": "### Merge-Modus\nautomatisch nach Review"}
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 1, "deletions": 0}]}
        return []

    merged = []

    def gh_text(*args, **kw):
        if args[:2] == ("pr", "merge"):
            merged.append(args[2])
        if args[:2] == ("pr", "diff"):
            return "diff --git a/a.py b/a.py\n+x\n"
        if args[0] == "api":
            return "# Regeln\n### Reviewer\nx\n## Goldene Regeln\ny\n"
        return ""

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", gh_text)
    monkeypatch.setattr(r, "call_llm", lambda model, msgs: (
        "**Freigegeben**\n\n### Muss\nkeine\n\n### Kann\n- keine", {"completion_tokens": 20, "finish_reason": "stop"}))
    r.run_once()
    assert merged == []
    assert "auto_merge=false" in capsys.readouterr().out
