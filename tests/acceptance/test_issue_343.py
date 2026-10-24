import importlib.util
import io
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_ohne_fallback", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_ohne_fallback"] = r
_spec.loader.exec_module(r)


def test_payload_schaltet_litellm_fallbacks_ab():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    payload = r.llm_payload("reviewer-go", messages)
    assert payload == {"model": "reviewer-go", "messages": messages, "max_tokens": r.MAX_TOKENS,
                       "disable_fallbacks": True}
    assert "tools" not in payload and "tool_choice" not in payload


def test_call_llm_schickt_disable_fallbacks(monkeypatch):
    seen = {}

    def urlopen(req, timeout=None):
        seen["data"] = json.loads(req.data)
        antwort = {"choices": [{"message": {"content": "**Freigegeben**"}, "finish_reason": "stop"}],
                   "usage": {"completion_tokens": 3}}
        return io.BytesIO(json.dumps(antwort).encode())

    monkeypatch.setattr(r.urllib.request, "urlopen", urlopen)
    text, _ = r.call_llm("reviewer-go", [{"role": "user", "content": "u"}])
    assert text == "**Freigegeben**"
    assert seen["data"]["disable_fallbacks"] is True
    assert seen["data"]["model"] == "reviewer-go"
