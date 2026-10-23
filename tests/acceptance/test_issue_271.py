import importlib.util
import json
from pathlib import Path
import sys

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_oc_stream", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_oc_stream"] = w
_spec.loader.exec_module(w)


def test_agent_cmd_opencode_behaelt_praefix_und_nutzt_json(monkeypatch):
    monkeypatch.setattr(w, "AGENT", "opencode")
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
    cmd = w.agent_cmd("Auftrag")
    assert cmd[:4] == ["opencode", "run", "-m", "litellm/coder"]
    assert "--format" in cmd and "json" in cmd
    assert cmd[-1] == "Auftrag"


def test_opencode_tool_use_und_text_werden_wie_qwen_geloggt(capsys):
    w.log_stream_line(json.dumps({
        "type": "text",
        "part": {"type": "text", "text": "Ich prüfe die Datei."},
    }))
    w.log_stream_line(json.dumps({
        "type": "tool_use",
        "part": {
            "tool": "bash",
            "state": {
                "status": "completed",
                "input": {"command": "pytest -q"},
                "output": "\n".join(f"zeile {i}" for i in range(20)),
                "metadata": {"exit": 0},
            },
        },
    }))
    w.log_stream_line(json.dumps({
        "type": "tool_use",
        "part": {
            "tool": "edit",
            "state": {
                "status": "completed",
                "input": {"filePath": "sandbox/worker_tick.py"},
                "output": "geändert",
            },
        },
    }))
    out = capsys.readouterr().out
    assert "Ich prüfe die Datei." in out
    assert "$ pytest -q" in out
    assert "    zeile 14" in out and "zeile 15" not in out and "(5 Zeilen mehr)" in out
    assert "→ edit sandbox/worker_tick.py" in out


def test_opencode_step_finish_formatiert_tokens_und_kosten(capsys):
    w.log_stream_line(json.dumps({
        "type": "step_finish",
        "part": {
            "reason": "stop",
            "cost": 0.0042,
            "tokens": {"input": 1500, "output": 250, "cache": {"read": 12000, "write": 0}},
        },
    }))
    out = capsys.readouterr().out.strip()
    assert out == "[OpenCode: stop, Token: 1500 ein/250 aus/12000 Cache, Kosten: $0.0042]"


def test_opencode_error_event_setzt_last_agent_error_ohne_key(monkeypatch, capsys):
    monkeypatch.setattr(w, "LAST_AGENT_ERROR", None, raising=False)
    w.log_stream_line(json.dumps({
        "type": "error",
        "error": {
            "name": "APIError",
            "data": {"message": "[API Error: 429 Rate limit reached Key=worker (sk-geheim12345)]"},
        },
    }))
    assert w.LAST_AGENT_ERROR == "API 429: Rate limit reached"
    out = capsys.readouterr().out
    assert "[Anbieterfehler] API 429: Rate limit reached" in out
    assert "sk-geheim" not in out
