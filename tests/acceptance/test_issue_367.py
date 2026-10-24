import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_oc_summe", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_oc_summe"] = w
_spec.loader.exec_module(w)

ISSUE_BODY = ("### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `sandbox/a.py`\n\n"
              "### Fertig, wenn\n\n- [ ] Tests gruen\n")


def _schritt(reason, tokens, cost=None):
    part = {"reason": reason, "tokens": tokens}
    if cost is not None:
        part["cost"] = cost
    return json.dumps({"type": "step_finish", "part": part})


def _lauf_mit_drei_schritten():
    w.log_stream_line(_schritt("tool-calls", {"input": 1000, "output": 50, "cache": {"read": 0, "write": 0}}, 0.01))
    w.log_stream_line(_schritt("tool-calls", {"input": 200, "output": 40, "cache": {"read": 900, "write": 0}}, 0.02))
    w.log_stream_line(_schritt("stop", {"input": 100, "output": 300, "cache": {"read": 1100, "write": 50}}, 0.03))


def test_summe_aller_schritte_mit_cache_in_der_eingabe(monkeypatch):
    monkeypatch.setattr(w, "OPENCODE_SUMME", None)
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    _lauf_mit_drei_schritten()
    u = w.agent_usage()
    assert u["input_tokens"] == 1000 + (200 + 900) + (100 + 1100 + 50)
    assert u["output_tokens"] == 50 + 40 + 300
    assert u["cache_read_input_tokens"] == 0 + 900 + 1100
    assert u["cost"] == pytest.approx(0.06)
    assert u["cache_read_input_tokens"] / u["input_tokens"] < 1


def test_last_agent_usage_bleibt_der_letzte_stop(monkeypatch):
    monkeypatch.setattr(w, "OPENCODE_SUMME", None)
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    _lauf_mit_drei_schritten()
    assert w.LAST_AGENT_USAGE == {"input_tokens": 100, "output_tokens": 300,
                                  "cache_read_input_tokens": 1100, "cost": 0.03}


def test_agent_usage_gibt_eine_kopie_und_qwen_bleibt_wie_es_war(monkeypatch):
    monkeypatch.setattr(w, "OPENCODE_SUMME", None)
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    assert w.agent_usage() is None
    w.log_stream_line(json.dumps({"type": "result", "usage": {
        "input_tokens": 11, "output_tokens": 2, "cache_read_input_tokens": 5, "cost": 0.5}}))
    assert w.OPENCODE_SUMME is None
    assert w.agent_usage()["input_tokens"] == 11
    w.log_stream_line(_schritt("stop", {"input": 1, "output": 1}))
    kopie = w.agent_usage()
    kopie["input_tokens"] = 999
    assert w.agent_usage()["input_tokens"] == 1


def test_kaputte_werte_werden_ignoriert_ohne_ausnahme(monkeypatch):
    monkeypatch.setattr(w, "OPENCODE_SUMME", None)
    w.opencode_schritt_summieren("kein dict")
    assert w.OPENCODE_SUMME is None
    w.opencode_schritt_summieren({"reason": "tool-calls", "tokens": {"input": "viele", "output": True,
                                                                     "cache": ["x"]}, "cost": "teuer"})
    assert w.OPENCODE_SUMME == {"input_tokens": None, "output_tokens": None,
                                "cache_read_input_tokens": None, "cost": 0.0}
    w.opencode_schritt_summieren({"reason": "tool-calls", "tokens": {"input": -5, "output": 7}, "cost": 1})
    assert w.OPENCODE_SUMME["input_tokens"] is None
    assert w.OPENCODE_SUMME["output_tokens"] == 7
    assert w.OPENCODE_SUMME["cost"] == 1.0


def test_main_schreibt_die_summe_ins_audit_und_beginnt_jeden_lauf_neu(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    kandidat = {"number": 7, "title": "Test Issue", "body": ISSUE_BODY,
                "author": {"login": w.OWNER}, "labels": [{"name": "bereit"}]}

    def gh(*args, **kwargs):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else [kandidat]
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Test Issue"}
        if args[:2] == ("issue", "edit"):
            return None
        if args[:2] in (("pr", "list"), ("run", "list")) or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    monkeypatch.setattr(w, "gh", gh)
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "open_pr", lambda n: True)
    monkeypatch.setattr(w, "choose_model", lambda *a: ("coder", []))
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)
    monkeypatch.setattr(w, "OPENCODE_SUMME", {"input_tokens": 999999, "output_tokens": 1,
                                              "cache_read_input_tokens": 1, "cost": 9.0})   # Rest eines früheren Laufs

    def lauf(prompt):
        _lauf_mit_drei_schritten()
        return 0

    monkeypatch.setattr(w, "run_agent", lauf)
    assert w.main() == 0
    zeilen = [json.loads(z) for z in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    fertig = [z for z in zeilen if z["event"] == "agent_run_finished"]
    assert len(fertig) == 1
    assert fertig[0]["tokens"]["input_tokens"] == 3350
    assert fertig[0]["tokens"]["output_tokens"] == 390
    assert fertig[0]["tokens"]["cache_read_input_tokens"] == 2000
    assert fertig[0]["cost"] == pytest.approx(0.06)
