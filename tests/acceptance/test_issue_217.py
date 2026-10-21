import importlib.util
import os
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_user_isolation", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_user_isolation"] = w
_spec.loader.exec_module(w)


def test_agent_user_constant_exists():
    assert getattr(w, "AGENT_USER", None) == "worker"


def test_run_agent_passes_user_when_root(monkeypatch):
    popen_kwargs = {}

    def mock_popen(cmd, **kwargs):
        popen_kwargs.update(kwargs)
        class DummyProc:
            stdout = iter([])
            returncode = 0
            def wait(self, timeout=None): return 0
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return DummyProc()

    monkeypatch.setattr(w.subprocess, "Popen", mock_popen)
    monkeypatch.setattr(w.os, "geteuid", lambda: 0)
    monkeypatch.setattr(w.os, "name", "posix")

    # pwd.getpwnam mocken, falls user auf Host nicht existiert
    class MockPw:
        pw_dir = "/home/worker"
    if hasattr(w, "pwd"):
        monkeypatch.setattr(w.pwd, "getpwnam", lambda u: MockPw())

    w.run_agent("echo test")
    assert popen_kwargs.get("user") == "worker"
    assert popen_kwargs.get("env", {}).get("HOME") == "/home/worker"
