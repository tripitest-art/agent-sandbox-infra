import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("worker_tick_pfad", ROOT / "sandbox" / "worker_tick.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_pfad"] = w
_spec.loader.exec_module(w)

SECRET = "geheim123"
VENV_PFAD = "/root/venv/bin:/root/.opencode/bin:/usr/bin:/bin"


class _FakeProc:
    def __init__(self):
        self.stdout, self.returncode = iter(()), 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _lauf(monkeypatch):
    seen = {}

    def popen(cmd, **kw):
        seen.update(kw)
        return _FakeProc()

    # So steht es auf dem Server: Systemd-PATH ohne venv, der Zusatz nur in ENV.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setitem(w.ENV, "PATH", VENV_PFAD)
    monkeypatch.setitem(w.ENV, "LITELLM_API_KEY", SECRET)
    monkeypatch.setattr(w.subprocess, "Popen", popen)
    w.run_agent("Auftrag")
    return seen["env"]


def test_agent_behaelt_den_pfad_aus_env(monkeypatch):
    env = _lauf(monkeypatch)
    assert env["PATH"] == VENV_PFAD


def test_agent_bekommt_trotzdem_keine_geheimnisse(monkeypatch):
    env = _lauf(monkeypatch)
    assert "LITELLM_API_KEY" not in env
    assert SECRET not in " ".join(str(v) for v in env.values())
