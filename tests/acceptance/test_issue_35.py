"""Akzeptanztests für Issue #<N>: Regel 5 – keine Geheimnisse im Agentenprozess und im Log."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("worker_tick", ROOT / "sandbox" / "worker_tick.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

SECRET = "geheim123"


class _FakeProc:
    def __init__(self, lines=(), rc=0):
        self.stdout, self.returncode = iter(lines), rc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_popen(seen, lines=()):
    def popen(cmd, **kw):
        seen.update(kw)
        return _FakeProc(lines)
    return popen


def test_agent_env_laesst_geheimnisse_weg():
    agent_env = getattr(w, "agent_env", None)
    assert agent_env is not None, "worker_tick.agent_env fehlt"
    env = agent_env({"PATH": "/usr/bin", "HOME": "/root", "LITELLM_API_KEY": SECRET,
                     "LITELLM_REVIEW_KEY": SECRET, "GITHUB_TOKEN": SECRET})
    assert env.get("PATH") == "/usr/bin" and env.get("HOME") == "/root"
    assert "LITELLM_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert SECRET not in " ".join(env.values())


def test_run_agent_gibt_keine_geheimnisse_mit(monkeypatch):
    seen = {}
    monkeypatch.setenv("LITELLM_API_KEY", SECRET)      # so steht der Schlüssel auf dem Server
    monkeypatch.setitem(w.ENV, "LITELLM_API_KEY", SECRET)
    monkeypatch.setattr(w.subprocess, "Popen", _fake_popen(seen))
    w.run_agent("Auftrag")
    env = seen.get("env") or {}
    assert "LITELLM_API_KEY" not in env
    assert SECRET not in " ".join(str(v) for v in env.values())


def test_log_stream_line_zeigt_keine_geheimnisse(monkeypatch, capsys):
    monkeypatch.setenv("LITELLM_API_KEY", SECRET)
    line = json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": f"LITELLM_API_KEY={SECRET}"}]}})
    w.log_stream_line(line)
    assert SECRET not in capsys.readouterr().out


def test_run_agent_zeigt_bei_opencode_keine_geheimnisse(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(w, "AGENT", "opencode")
    monkeypatch.setenv("LITELLM_API_KEY", SECRET)
    monkeypatch.setattr(w.subprocess, "Popen", _fake_popen(seen, lines=[f"LITELLM_API_KEY={SECRET}\n"]))
    w.run_agent("Auftrag")
    assert SECRET not in capsys.readouterr().out


def test_redact_ersetzt_geheimniswerte():
    redact = getattr(w, "redact", None)
    assert redact is not None, "worker_tick.redact fehlt"
    line = redact(f"LITELLM_API_KEY={SECRET} (Rest bleibt)", [SECRET])
    assert SECRET not in line
    assert "***" in line and "Rest bleibt" in line
