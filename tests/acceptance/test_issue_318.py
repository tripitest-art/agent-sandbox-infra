import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_git_user", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_git_user"] = w
_spec.loader.exec_module(w)


def _als_agentennutzer(monkeypatch, nutzer="worker"):
    """Rechte-Drop einschalten: Opt-in-Variable und geteuid=0 werden gefaked,
    nie ein echtes `chown` und nie ein echter Nutzerwechsel."""
    monkeypatch.setattr(w, "GIT_USER", nutzer)
    monkeypatch.setattr(w.os, "geteuid", lambda: 0, raising=False)


def test_agent_run_kwargs_ohne_opt_in_leer(monkeypatch):
    monkeypatch.setattr(w, "GIT_USER", "")
    assert w.agent_run_kwargs() == {}


def test_agent_run_kwargs_als_root_mit_opt_in(monkeypatch):
    _als_agentennutzer(monkeypatch)
    assert w.agent_run_kwargs() == {"user": "worker", "group": "worker"}


def test_agent_run_kwargs_ohne_root_leer(monkeypatch):
    monkeypatch.setattr(w, "GIT_USER", "worker")
    monkeypatch.setattr(w.os, "geteuid", lambda: 424242, raising=False)
    assert w.agent_run_kwargs() == {}


def test_prepare_workdir_legt_einen_neuen_worktree_als_agentennutzer_an(monkeypatch, tmp_path):
    klon = tmp_path / "klon"
    klon.mkdir()
    wt = tmp_path / "worktrees" / "w2"
    calls = []
    monkeypatch.setattr(w, "BASE_WORKDIR", str(klon))
    monkeypatch.setattr(w, "WORKDIR", str(wt))
    _als_agentennutzer(monkeypatch)

    def fake_run(cmd, cwd=None, **kw):
        calls.append((list(cmd), kw))
        if list(cmd[:3]) == ["git", "worktree", "add"]:
            wt.mkdir(parents=True)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    w.prepare_workdir()

    assert [c for c, _ in calls] == [
        ["git", "fetch", "-q", "origin"],
        ["git", "worktree", "add", "--force", "--detach", str(wt), "origin/main"],
    ]
    fetch_kw, add_kw = calls[0][1], calls[1][1]
    assert "user" not in fetch_kw and "group" not in fetch_kw        # fetch bleibt root
    assert add_kw.get("user") == "worker" and add_kw.get("group") == "worker"


def test_prepare_workdir_checktoutet_bestehende_baeume_als_agentennutzer(monkeypatch, tmp_path):
    klon = tmp_path / "klon"
    klon.mkdir()
    wt = tmp_path / "worktrees" / "w2"
    wt.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(w, "BASE_WORKDIR", str(klon))
    monkeypatch.setattr(w, "WORKDIR", str(wt))
    _als_agentennutzer(monkeypatch)

    def fake_run(cmd, cwd=None, **kw):
        calls.append((list(cmd), kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    w.prepare_workdir()

    assert [c for c, _ in calls] == [
        ["git", "fetch", "-q", "origin"],
        ["git", "checkout", "-qf", "--detach", "origin/main"],
        ["git", "reset", "-q", "--hard", "origin/main"],
        ["git", "clean", "-qfd"],
    ]
    assert "user" not in calls[0][1]
    for _, kw in calls[1:]:
        assert kw.get("user") == "worker" and kw.get("group") == "worker"


def test_prepare_workdir_ohne_git_user_bleibt_wie_bisher(monkeypatch, tmp_path):
    klon = tmp_path / "klon"
    klon.mkdir()
    wt = tmp_path / "worktrees" / "w2"
    wt.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(w, "BASE_WORKDIR", str(klon))
    monkeypatch.setattr(w, "WORKDIR", str(wt))
    monkeypatch.setattr(w, "GIT_USER", "")

    def fake_run(cmd, cwd=None, **kw):
        calls.append(kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    w.prepare_workdir()
    assert all("user" not in kw and "group" not in kw for kw in calls)


def test_sync_mergt_den_main_als_agentennutzer(monkeypatch, tmp_path):
    arbeit = tmp_path / "wt"
    arbeit.mkdir()
    monkeypatch.setattr(w, "WORKDIR", str(arbeit))
    _als_agentennutzer(monkeypatch)
    calls = []

    def fake_run(cmd, **kw):
        calls.append((list(cmd), kw))
        rc = 1 if list(cmd[:2]) in (["git", "merge-base"], ["git", "merge"]) else 0
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    assert w.sync_issue_branch_with_main(7) is False

    assert [c for c, _ in calls] == [
        ["git", "fetch", "-q", "origin"],
        ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        ["git", "merge", "--no-edit", "origin/main"],
        ["git", "merge", "--abort"],
    ]
    assert "user" not in calls[0][1] and "user" not in calls[1][1]   # fetch und merge-base bleiben root
    assert calls[2][1].get("user") == "worker"
    assert calls[3][1].get("user") == "worker"
    assert all(kw.get("env") == w.ENV for _, kw in calls)
