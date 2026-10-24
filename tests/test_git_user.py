"""Tests für die Rechte-Trennung der schreibenden git-Befehle (#318, Teil 2 zu #280).

Alle Fälle faken `geteuid`/`os` oder `subprocess.run`; kein echter Nutzerwechsel, kein `chown`,
kein git und kein Netz. Die Akzeptanztests aus dem Issue liegen zusätzlich in
`tests/acceptance/test_issue_318.py`; hier stehen die Randfälle (FakeOs aus #40, Windows,
Hauptbaum-Modus, `prepare_issue_frame`).
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_git_user", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_git_user"] = w
_spec.loader.exec_module(w)


def _als_agentennutzer(monkeypatch, nutzer="worker"):
    """Opt-in einschalten und root vortäuschen – nie ein echter Rechtewechsel."""
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


def test_agent_run_kwargs_mit_fake_os_aus_issue_40_leer(monkeypatch):
    """Der FakeOs aus #40 kennt weder `name` noch `geteuid`; die Prüfreihenfolge muss halten."""
    monkeypatch.setattr(w, "GIT_USER", "worker")
    monkeypatch.setattr(w, "os", type("FakeOs", (), {}))
    assert w.agent_run_kwargs() == {}


def test_agent_run_kwargs_unter_windows_leer(monkeypatch):
    """Auf Windows gibt es den Subprozess-Rechte-Drop nicht."""
    monkeypatch.setattr(w, "GIT_USER", "worker")
    monkeypatch.setattr(w, "os", type("FakeOs", (), {
        "name": "nt",
        "geteuid": staticmethod(lambda: 0),
    }))
    assert w.agent_run_kwargs() == {}


def test_prepare_workdir_im_hauptbaum_gibt_schreibende_befehle_ab(monkeypatch, tmp_path):
    """Auch ohne Worktree: fetch bleibt root, checkout/reset/clean gehören dem Agenten."""
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    monkeypatch.setattr(w, "BASE_WORKDIR", str(arbeit))
    monkeypatch.setattr(w, "WORKDIR", str(arbeit))
    _als_agentennutzer(monkeypatch)
    calls = []

    def fake_run(cmd, cwd=None, **kw):
        calls.append((list(cmd), kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    w.prepare_workdir()

    assert [c for c, _ in calls] == [
        ["git", "fetch", "-q", "origin"],
        ["git", "checkout", "-qf", "main"],
        ["git", "reset", "-q", "--hard", "origin/main"],
        ["git", "clean", "-qfd"],
    ]
    assert "user" not in calls[0][1] and "group" not in calls[0][1]
    for _, kw in calls[1:]:
        assert kw.get("user") == "worker" and kw.get("group") == "worker"


def test_prepare_workdir_ohne_opt_in_bleibt_bei_root(monkeypatch, tmp_path):
    """Ohne GIT_USER (heutiger Betrieb) bekommt kein Aufruf `user`/`group`."""
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    monkeypatch.setattr(w, "BASE_WORKDIR", str(arbeit))
    monkeypatch.setattr(w, "WORKDIR", str(arbeit))
    monkeypatch.setattr(w, "GIT_USER", "")
    calls = []

    def fake_run(cmd, cwd=None, **kw):
        calls.append(kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    w.prepare_workdir()
    assert all("user" not in kw and "group" not in kw for kw in calls)


def test_prepare_issue_frame_sichert_den_test_als_agentennutzer(monkeypatch, tmp_path):
    """checkout -B, add und commit (#234) laufen mit dem Rechte-Drop."""
    arbeit = tmp_path / "wt"
    arbeit.mkdir()
    monkeypatch.setattr(w, "WORKDIR", str(arbeit))
    _als_agentennutzer(monkeypatch)
    body = "### Akzeptanztests\n\n```python\nassert True\n```\n"
    monkeypatch.setattr(w, "gh", lambda *args: {"body": body})
    calls = []

    def fake_run(cmd, **kw):
        calls.append((list(cmd), kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    assert w.prepare_issue_frame(42) == arbeit / "tests" / "acceptance" / "test_issue_42.py"

    assert [c for c, _ in calls] == [
        ["git", "checkout", "-B", "feature/issue-42"],
        ["git", "add", "tests/acceptance/test_issue_42.py"],
        ["git", "commit", "-m", "Akzeptanztests aus #42"],
    ]
    for _, kw in calls:
        assert kw.get("cwd") == str(arbeit)
        assert kw.get("env") == w.ENV
        assert kw.get("check") is True
        assert kw.get("user") == "worker" and kw.get("group") == "worker"
