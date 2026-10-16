import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_issue_usage", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_issue_usage"] = w
_spec.loader.exec_module(w)


def test_format_usage_voll():
    s = w.format_usage({"input_tokens": 1200, "output_tokens": 340, "cache_read_input_tokens": 9800})
    assert s == ", Token: 1200 ein/340 aus/9800 Cache"


def test_format_usage_teilweise_und_unbrauchbar():
    assert w.format_usage({"output_tokens": 5}) == ", Token: 0 ein/5 aus/0 Cache"
    for bad in (None, "x", [], {}, {"input_tokens": -1}, {"input_tokens": "7"}, {"output_tokens": True}):
        assert w.format_usage(bad) == ""


def test_result_zeile_mit_und_ohne_usage(capsys):
    base = {"type": "result", "subtype": "success", "num_turns": 3, "duration_ms": 46770}
    w.log_stream_line(json.dumps(base))
    mit = dict(base, usage={"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 90})
    w.log_stream_line(json.dumps(mit))
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "[Qwen Code: success, 3 Züge, 47 s]"
    assert lines[1] == "[Qwen Code: success, 3 Züge, 47 s, Token: 10 ein/2 aus/90 Cache]"
