"""Tests für LAST_AGENT_USAGE bei OpenCode-Events (#316), ohne Netzwerk.

`agent_run_finished` (#222) soll für beide Agenten dieselbe Datenbasis tragen: Qwen Code
befüllt `LAST_AGENT_USAGE` aus dem `result`-Event, OpenCode seit #316 aus `step_finish`
mit reason "stop".
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_opencode_usage", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_opencode_usage"] = w
_spec.loader.exec_module(w)


@pytest.fixture(autouse=True)
def _frisch(monkeypatch):
    monkeypatch.setattr(w, "LAST_AGENT_USAGE", None)


def _stream(event: dict) -> None:
    w.log_stream_line(json.dumps(event))


def _stop(part: dict) -> dict:
    return {"type": "step_finish", "part": {"reason": "stop", **part}}


def test_stop_event_belegt_alle_felder():
    _stream(_stop({"tokens": {"input": 1200, "output": 340, "cache": {"read": 9800}},
                   "cost": 0.0103}))
    assert w.LAST_AGENT_USAGE == {"input_tokens": 1200,
                                  "output_tokens": 340,
                                  "cache_read_input_tokens": 9800,
                                  "cost": 0.0103}


def test_fehlende_teile_werden_none_und_cost_null():
    _stream(_stop({"tokens": {"input": 7, "cache": {"read": 3}}}))
    assert w.LAST_AGENT_USAGE == {"input_tokens": 7,
                                  "output_tokens": None,
                                  "cache_read_input_tokens": 3,
                                  "cost": 0.0}
    assert isinstance(w.LAST_AGENT_USAGE["cost"], float)


def test_fehlendes_tokens_dict_ergibt_nur_none_und_cost():
    _stream(_stop({"cost": 2}))
    assert w.LAST_AGENT_USAGE == {"input_tokens": None,
                                  "output_tokens": None,
                                  "cache_read_input_tokens": None,
                                  "cost": 2.0}
    assert isinstance(w.LAST_AGENT_USAGE["cost"], float)


def test_cache_muss_dict_sein():
    _stream(_stop({"tokens": {"input": 1, "output": 2, "cache": ["read", 3]}}))
    assert w.LAST_AGENT_USAGE["cache_read_input_tokens"] is None


@pytest.mark.parametrize("wert", ["1200", 12.5, None, [1], True])
def test_nicht_int_tokens_werden_none(wert):
    _stream(_stop({"tokens": {"input": wert, "output": wert, "cache": {"read": wert}}}))
    assert w.LAST_AGENT_USAGE["input_tokens"] is None
    assert w.LAST_AGENT_USAGE["output_tokens"] is None
    assert w.LAST_AGENT_USAGE["cache_read_input_tokens"] is None


@pytest.mark.parametrize("wert", ["teuer", None, [1], {"betrag": 1}, True])
def test_ungueltiger_cost_wird_0_0(wert):
    _stream(_stop({"tokens": {"input": 1}, "cost": wert}))
    assert w.LAST_AGENT_USAGE["cost"] == 0.0
    assert isinstance(w.LAST_AGENT_USAGE["cost"], float)


def test_nur_stop_aendert_die_usage():
    _stream({"type": "step_finish", "part": {"reason": "tool-calls",
                                             "tokens": {"input": 5}, "cost": 1}})
    assert w.LAST_AGENT_USAGE is None


def test_part_ohne_dict_aendert_die_usage_nicht():
    _stream(_stop({"tokens": {"input": 4}}))
    vorher = dict(w.LAST_AGENT_USAGE)
    _stream({"type": "step_finish", "part": "kein dict"})
    _stream({"type": "step_finish", "part": ["kein", "dict"]})
    assert w.LAST_AGENT_USAGE == vorher


def test_zweiter_stop_ueberschreibt_das_dict():
    _stream(_stop({"tokens": {"input": 4}, "cost": 1.5}))
    alt = w.LAST_AGENT_USAGE
    _stream(_stop({"tokens": {"input": 9}, "cost": 0.25}))
    assert w.LAST_AGENT_USAGE == {"input_tokens": 9,
                                  "output_tokens": None,
                                  "cache_read_input_tokens": None,
                                  "cost": 0.25}
    assert alt["input_tokens"] == 4          # das alte dict bleibt unberührt


def test_logzeile_bleibt_unveraendert(capsys):
    _stream(_stop({"tokens": {"input": 5, "output": 2}, "cost": 0.5}))
    assert capsys.readouterr().out == "[OpenCode: stop, Token: 5 ein/2 aus/0 Cache, " \
                                     "Kosten: $0.5000]\n"


def test_kaputte_werte_brechen_den_logpfad_nicht(capsys):
    _stream(_stop({"tokens": {"input": "viele", "cache": "kein dict"}, "cost": "teuer"}))
    assert w.LAST_AGENT_USAGE == {"input_tokens": None,
                                  "output_tokens": None,
                                  "cache_read_input_tokens": None,
                                  "cost": 0.0}
    assert capsys.readouterr().out == "[OpenCode: stop]\n"


def test_qwen_result_belegt_weiterhin_die_usage():
    """Kontrakt: der Qwen-Zweig bleibt unverändert und liefert dieselbe Struktur."""
    _stream({"type": "result", "usage": {"input_tokens": 11, "output_tokens": 2,
                                        "cache_read_input_tokens": 0, "cost": 0.5}})
    assert w.LAST_AGENT_USAGE == {"input_tokens": 11, "output_tokens": 2,
                                  "cache_read_input_tokens": 0, "cost": 0.5}


# --- #367: Summe aller `step_finish`, `input_tokens` inklusive Cache ---


def _schritt(reason: str, tokens: dict, cost: float | None = None) -> None:
    part: dict = {"reason": reason, "tokens": tokens}
    if cost is not None:
        part["cost"] = cost
    _stream({"type": "step_finish", "part": part})


def test_summe_zaehlt_alle_schritte_und_cache_in_der_eingabe(monkeypatch):
    monkeypatch.setattr(w, "OPENCODE_SUMME", None)
    _schritt("tool-calls", {"input": 1000, "output": 50, "cache": {"read": 0, "write": 0}}, 0.01)
    _schritt("tool-calls", {"input": 200, "output": 40, "cache": {"read": 900, "write": 0}}, 0.02)
    _schritt("stop", {"input": 100, "output": 300, "cache": {"read": 1100, "write": 50}}, 0.03)
    assert w.agent_usage() == {"input_tokens": 1000 + 200 + 900 + 100 + 1100 + 50,
                               "output_tokens": 50 + 40 + 300,
                               "cache_read_input_tokens": 0 + 900 + 1100,
                               "cost": pytest.approx(0.06)}


def test_zwei_laeufe_nacheinander_ohne_uebertrag(monkeypatch):
    monkeypatch.setattr(w, "OPENCODE_SUMME", None)
    _schritt("stop", {"input": 10, "output": 1}, 0.1)
    erster = w.agent_usage()
    # main() setzt das Global vor dem nächsten Lauf zurück; der zweite Lauf beginnt bei null.
    monkeypatch.setattr(w, "OPENCODE_SUMME", None)
    _schritt("stop", {"input": 5, "output": 2}, 0.2)
    assert erster["input_tokens"] == 10
    assert w.agent_usage() == {"input_tokens": 5, "output_tokens": 2,
                               "cache_read_input_tokens": None, "cost": pytest.approx(0.2)}


def test_schritt_ohne_cache_zaehlt_nur_input(monkeypatch):
    monkeypatch.setattr(w, "OPENCODE_SUMME", None)
    _schritt("stop", {"input": 7, "output": 3})
    assert w.agent_usage() == {"input_tokens": 7, "output_tokens": 3,
                               "cache_read_input_tokens": None, "cost": 0.0}


def test_schritt_ohne_tokens_aendert_die_summe_nicht(monkeypatch):
    monkeypatch.setattr(w, "OPENCODE_SUMME", None)
    _schritt("stop", {"input": 4, "output": 2}, 0.5)
    _stream({"type": "step_finish", "part": {"reason": "tool-calls"}})
    assert w.agent_usage() == {"input_tokens": 4, "output_tokens": 2,
                               "cache_read_input_tokens": None, "cost": pytest.approx(0.5)}

