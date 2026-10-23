import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_oc_usage", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_oc_usage"] = w
_spec.loader.exec_module(w)


def _stream(event: dict) -> None:
    w.log_stream_line(json.dumps(event))


def test_step_finish_stop_belegt_usage_und_cost(monkeypatch):
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    _stream({"type": "step_finish", "part": {"reason": "stop",
             "tokens": {"input": 1200, "output": 340, "cache": {"read": 9800}},
             "cost": 0.0103}})
    assert w.LAST_AGENT_USAGE == {"input_tokens": 1200, "output_tokens": 340,
                                  "cache_read_input_tokens": 9800, "cost": 0.0103}


def test_step_finish_stop_ohne_cost_liefert_0_0(monkeypatch):
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    _stream({"type": "step_finish", "part": {"reason": "stop",
             "tokens": {"input": 1, "output": 1}}})
    assert w.LAST_AGENT_USAGE["cost"] == 0.0
    assert isinstance(w.LAST_AGENT_USAGE["cost"], float)


def test_step_finish_fehlende_token_keys_werden_none(monkeypatch):
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    _stream({"type": "step_finish", "part": {"reason": "stop", "cost": 2.5,
             "tokens": {"input": 7, "cache": {"read": 3}}}})
    assert w.LAST_AGENT_USAGE == {"input_tokens": 7, "output_tokens": None,
                                  "cache_read_input_tokens": 3, "cost": 2.5}


def test_kein_stop_laesst_usage_unberuehrt(monkeypatch):
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    _stream({"type": "step_finish", "part": {"reason": "tool-calls",
             "tokens": {"input": 5, "output": 5}, "cost": 1}})
    assert w.LAST_AGENT_USAGE is None


def test_kaputte_werte_brechen_den_logpfad_nicht(monkeypatch):
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    _stream({"type": "step_finish", "part": {"reason": "stop",
             "tokens": {"input": "viele", "cache": ["x"]}, "cost": "teuer"}})
    assert w.LAST_AGENT_USAGE == {"input_tokens": None, "output_tokens": None,
                                  "cache_read_input_tokens": None, "cost": 0.0}
    _stream({"type": "step_finish", "part": {"reason": "stop",
             "tokens": {"input": 2}, "cost": True}})
    assert w.LAST_AGENT_USAGE["input_tokens"] == 2
    assert w.LAST_AGENT_USAGE["cost"] == 0.0
    _stream({"type": "step_finish", "part": "kein dict"})
    assert w.LAST_AGENT_USAGE["input_tokens"] == 2


def test_qwen_result_setzt_usage_weiter(monkeypatch):
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    _stream({"type": "result", "usage": {"input_tokens": 11, "output_tokens": 2,
             "cache_read_input_tokens": 0, "cost": 0.5}})
    assert w.LAST_AGENT_USAGE["input_tokens"] == 11
    assert w.LAST_AGENT_USAGE["cost"] == 0.5
