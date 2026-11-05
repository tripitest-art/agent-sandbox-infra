import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_agent_pwd", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_agent_pwd"] = w
_spec.loader.exec_module(w)


class _Proc:
    returncode = 0
    stdout: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def poll(self):
        return 0


def _starte(monkeypatch, tmp_path, env):
    kw = {}
    monkeypatch.setattr(w.subprocess, "Popen", lambda cmd, **kwargs: kw.update(kwargs) or _Proc())
    monkeypatch.setattr(w, "agent_identitaet", lambda: (None, None))
    monkeypatch.setattr(w, "workdir_zustand", lambda wd: None)
    monkeypatch.setattr(w, "_waechter", lambda *a: None)
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(w, "RUN_TIMEOUT", 60)
    monkeypatch.setattr(w, "ENV", env)
    assert w.run_agent("prompt") == 0
    return kw


def test_pwd_aus_dem_wrapper_wird_durch_den_worktree_ersetzt(monkeypatch, tmp_path):
    kw = _starte(monkeypatch, tmp_path, {"PATH": "/usr/bin:/bin", "PWD": "/"})
    assert kw["cwd"] == str(tmp_path)
    assert kw["env"]["PWD"] == str(tmp_path)


def test_pwd_wird_gesetzt_auch_wenn_env_keins_hat(monkeypatch, tmp_path):
    kw = _starte(monkeypatch, tmp_path, {"PATH": "/usr/bin:/bin"})
    assert kw["env"]["PWD"] == str(tmp_path)
    assert kw["env"]["PATH"] == "/usr/bin:/bin"
