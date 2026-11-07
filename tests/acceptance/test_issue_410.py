import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_pr_rahmen", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_pr_rahmen"] = w
_spec.loader.exec_module(w)

BRANCH = "feature/issue-100"
REVIEW = ("**Änderungen nötig**\n\n### Muss\n- `f` prüft None nicht (sandbox/a.py).\n"
          "- Im PR-Text fehlt „Closes #100“.")


def _pr(number=103, branch=BRANCH, body="x"):
    return {"number": number, "headRefName": branch, "headRefOid": "3373e35",
            "isCrossRepository": False, "commits": [{"committedDate": "2026-09-29T21:37:00Z"}],
            "reviews": [{"submittedAt": "2026-09-29T21:39:23Z", "body": REVIEW,
                         "author": {"login": w.OWNER}, "commit": {"oid": "3373e35"}}],
            "title": "T", "body": body}


class FakeGH:
    def __init__(self, prs=(), head=BRANCH, edit_fehler=False):
        self.prs, self.head, self.edit_fehler = list(prs), head, edit_fehler
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return self.prs
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": w.LABEL}], "title": "Titel"}
        if args[:2] == ("pr", "view"):
            if "headRefName" in args and len(args) == 5:
                return {"headRefName": self.head}
            return {**_pr(), "state": "OPEN"}
        if args[:2] == ("pr", "edit"):
            if self.edit_fehler:
                raise RuntimeError("gh nicht erreichbar")
            return None
        if args[:2] == ("pr", "comment"):
            return None
        if args[:2] in (("issue", "list"), ("run", "list")) or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


class FakeRun:
    def __init__(self, ahead="0", push_rc=0, push_out=""):
        self.ahead, self.push_rc, self.push_out = ahead, push_rc, push_out
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "rev-list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{self.ahead}\n", stderr="")
        if cmd[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(cmd, self.push_rc, stdout=self.push_out, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def cmds(self):
        return [c for c, _ in self.calls]


def test_prepare_pr_frame_checkt_den_pr_branch_aus(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(w, "gh", FakeGH())
    monkeypatch.setattr(w, "agent_run_kwargs", lambda: {})
    run = FakeRun()
    monkeypatch.setattr(w.subprocess, "run", run)
    assert w.prepare_pr_frame(103) == BRANCH
    assert run.cmds() == [["git", "fetch", "-q", "origin", BRANCH],
                          ["git", "checkout", "-qfB", BRANCH, f"origin/{BRANCH}"]]
    assert all(kw.get("cwd") == str(tmp_path) and kw.get("check") is True for _, kw in run.calls)


def test_prepare_pr_frame_ohne_workdir_oder_mit_fremdem_branch(monkeypatch, tmp_path):
    run = FakeRun()
    monkeypatch.setattr(w.subprocess, "run", run)
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path / "fehlt"))
    fake = FakeGH()
    monkeypatch.setattr(w, "gh", fake)
    assert w.prepare_pr_frame(103) is None
    assert fake.calls == [] and run.calls == []

    zeilen = []
    monkeypatch.setattr(w, "log", zeilen.append)
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(w, "gh", FakeGH(head="main; rm -rf /"))
    assert w.prepare_pr_frame(103) is None
    assert run.calls == []
    assert zeilen == ["pr103: kein Worker-Branch, Nacharbeit übersprungen."]


def test_prepare_pr_frame_git_fehler(monkeypatch, tmp_path):
    def kaputt(cmd, **kwargs):
        raise subprocess.CalledProcessError(128, cmd)
    zeilen = []
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(w, "gh", FakeGH())
    monkeypatch.setattr(w.subprocess, "run", kaputt)
    monkeypatch.setattr(w, "log", zeilen.append)
    assert w.prepare_pr_frame(103) is None
    assert zeilen == ["pr103: PR-Branch nicht ausgecheckt (CalledProcessError)."]


def test_finish_pr_frame_pusht_postet_und_setzt_den_text(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    (tmp_path / ".agent").mkdir()
    frage = tmp_path / ".agent" / "frage.md"
    frage.write_text("Muss-Punkt 2 widerspricht dem Issue.", encoding="utf-8")
    body = tmp_path / ".agent" / "pr-body.md"
    body.write_text("## Was und warum\n\nCloses #100\n", encoding="utf-8")
    fake = FakeGH()
    monkeypatch.setattr(w, "gh", fake)
    monkeypatch.setattr(w, "push_env", lambda: {"PUSH": "1"})
    run = FakeRun(ahead="2")
    monkeypatch.setattr(w.subprocess, "run", run)
    assert w.finish_pr_frame(103, BRANCH) is True
    push = [(c, kw) for c, kw in run.calls if c[:2] == ["git", "push"]]
    assert [c for c, _ in push] == [["git", "push", "origin", f"HEAD:{BRANCH}"]]
    assert push[0][1].get("env") == {"PUSH": "1"}
    assert ("pr", "comment", "103", "--body-file", str(frage)) in fake.calls
    assert ("pr", "edit", "103", "--body-file", str(body)) in fake.calls
    assert not frage.exists() and not body.exists()


def test_finish_pr_frame_ohne_neue_commits_und_ohne_dateien(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    fake = FakeGH()
    monkeypatch.setattr(w, "gh", fake)
    run = FakeRun(ahead="0")
    monkeypatch.setattr(w.subprocess, "run", run)
    assert w.finish_pr_frame(103, BRANCH) is True
    assert not any(c[:2] == ["git", "push"] for c in run.cmds())
    assert ["git", "rev-list", "--count", f"origin/{BRANCH}..HEAD"] in run.cmds()
    assert fake.calls == []


def test_finish_pr_frame_abgelehnter_push_alarmiert_und_setzt_den_text_trotzdem(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "pr-body.md").write_text("Text", encoding="utf-8")
    fake = FakeGH()
    monkeypatch.setattr(w, "gh", fake)
    monkeypatch.setattr(w, "push_env", lambda: {})
    monkeypatch.setattr(w.subprocess, "run",
                        FakeRun(ahead="1", push_rc=1, push_out="FAILED tests/test_a.py::test_x - assert 1\n"))
    alarme, zeilen = [], []
    monkeypatch.setattr(w, "alarm", lambda skey, err: alarme.append((skey, err)))
    monkeypatch.setattr(w, "log", zeilen.append)
    assert w.finish_pr_frame(103, BRANCH) is False
    assert alarme == [("pr103", "Push abgelehnt (Tests rot oder GitHub-Fehler)")]
    assert "pr103: Push abgelehnt, rote Tests: tests/test_a.py::test_x" in zeilen
    assert any(c[:2] == ("pr", "edit") for c in fake.calls)


def test_nacharbeit_auftrag_ohne_gh(monkeypatch):
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", "## Was und warum\n\nCloses #\n")
    monkeypatch.setattr(w, "gh", FakeGH(prs=[_pr()]))
    _, task, n, _ = w.rework_job()
    assert n == 103
    for verboten in ("gh pr", "gh issue", "git push"):
        assert verboten not in task, verboten
    assert task.startswith("Du arbeitest ein Review ab")
    assert "Run these steps sequentially" in task
    assert BRANCH in task and REVIEW in task and w.agent_test_cmd() in task
    assert "`.agent/pr-body.md`" in task and "„Closes #100“" in task
    assert f"„Worker: {w.WORKER}“" in task
    assert "git log -1 --oneline" in task
    assert "Do not push; the tick pushes." in task
    assert "Never approve or merge the PR yourself" in task


def test_textauftrag_ohne_gh(monkeypatch):
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", "## Was und warum\n\nCloses #\n")
    pr = _pr(number=112, branch="feature/issue-102")
    pr["reviews"] = []
    monkeypatch.setattr(w, "gh", FakeGH(prs=[pr], edit_fehler=True))
    _, task, n, _ = w.text_fix_job()
    assert n == 112
    for verboten in ("gh pr", "gh issue", "git push"):
        assert verboten not in task, verboten
    assert task.startswith("Im Text von PR #112")
    assert "no code" in task and "Run these steps sequentially" in task
    assert "feature/issue-102" in task
    assert "git diff --stat origin/main...HEAD" in task
    assert "`.agent/pr-body.md`" in task and "„Closes #102“" in task


def _main_mit_nacharbeit(monkeypatch, tmp_path, branch):
    work = tmp_path / "w1"
    work.mkdir()
    monkeypatch.setattr(w, "WORKDIR", str(work))
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", "## Was und warum\n\nCloses #\n")
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "choose_model", lambda *a: ("coder", []))
    monkeypatch.setattr(w, "agent_identitaet", lambda: (None, None))
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "ensure_pre_push_hook", lambda: None)
    monkeypatch.setattr(w, "ablage_vorbereiten", lambda *a, **k: True)
    monkeypatch.setattr(w.subprocess, "run", FakeRun())
    monkeypatch.setattr(w, "gh", FakeGH(prs=[_pr()]))
    ablauf = []
    monkeypatch.setattr(w, "prepare_pr_frame", lambda pr_no: ablauf.append(("prepare", pr_no)) or branch)
    monkeypatch.setattr(w, "finish_pr_frame", lambda pr_no, b: ablauf.append(("finish", pr_no, b)) or True)
    monkeypatch.setattr(w, "run_agent", lambda prompt: ablauf.append(("agent",)) or 0)
    assert w.main() == 0
    return ablauf


def test_main_rahmt_die_nacharbeit_ein(monkeypatch, tmp_path):
    ablauf = _main_mit_nacharbeit(monkeypatch, tmp_path, BRANCH)
    assert ablauf == [("prepare", 103), ("agent",), ("finish", 103, BRANCH)]


def test_main_ohne_ausgecheckten_branch_startet_keinen_agenten(monkeypatch, tmp_path):
    zeilen = []
    monkeypatch.setattr(w, "log", zeilen.append)
    ablauf = _main_mit_nacharbeit(monkeypatch, tmp_path, None)
    assert ablauf == [("prepare", 103)]
    assert "pr103@2026-09-29T21:39:23Z: PR-Branch nicht ausgecheckt, Versuch zählt nicht." in zeilen
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state.get("pr103@2026-09-29T21:39:23Z", 0) == 0
