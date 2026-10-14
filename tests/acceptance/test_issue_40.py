import importlib.util
import os
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

BASE = "/base"
WORK = "/base-worktrees/w2"


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_os(exists):
    """Nur was prepare_workdir braucht. Ohne O_EXCL entfällt der flock-Zweig,
    damit der Test auf Windows und Linux denselben Ablauf sieht."""
    return type("FakeOs", (), {
        "path": type("FakePath", (), {
            "exists": staticmethod(exists),
            "join": staticmethod(os.path.join),
            "dirname": staticmethod(os.path.dirname),
        }),
        "makedirs": staticmethod(lambda *a, **k: None),
    })


def _prepare(monkeypatch, exists):
    calls = []
    monkeypatch.setattr(w, "BASE_WORKDIR", BASE)
    monkeypatch.setattr(w, "WORKDIR", WORK)
    monkeypatch.setattr(w, "os", _fake_os(exists))
    monkeypatch.setattr(w, "open", lambda *a, **k: _Null(), raising=False)
    monkeypatch.setattr(w.subprocess, "run", lambda cmd, cwd=None, **kw: calls.append(list(cmd)))
    w.prepare_workdir()
    return calls


def test_new_worktree_is_created_detached(monkeypatch):
    # `-B main` scheitert, sobald ein anderer Slot `main` belegt (exit 255).
    calls = _prepare(monkeypatch, lambda p: p == BASE)
    assert calls == [["git", "fetch", "-q", "origin"],
                     ["git", "worktree", "add", "--force", "--detach", WORK, "origin/main"]]
    for cmd in calls:
        assert "-B" not in cmd and "main" not in cmd


def test_existing_worktree_is_reused_detached(monkeypatch):
    # `checkout -qf main` scheitert dort ebenso (exit 128) und killt den Takt nach dem Claim.
    calls = _prepare(monkeypatch, lambda p: True)
    assert calls == [["git", "fetch", "-q", "origin"],
                     ["git", "checkout", "-qf", "--detach", "origin/main"],
                     ["git", "reset", "-q", "--hard", "origin/main"],
                     ["git", "clean", "-qfd"]]
    for cmd in calls:
        assert "-B" not in cmd and "main" not in cmd
