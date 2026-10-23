"""Tests für die Token-Kennzahlen in sandbox/dashboard_stats.py (#322), ohne Netzwerk."""

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_MODUL = Path(__file__).resolve().parents[1] / "sandbox" / "dashboard_stats.py"
_spec = importlib.util.spec_from_file_location("dashboard_stats_token_stats", _MODUL)
st = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_stats_token_stats"] = st
_spec.loader.exec_module(st)

NOW = datetime(2026, 10, 23, 12, 0, 0)
H = timedelta(hours=1)
SCHLUESSEL = {"laeufe", "mit_token", "ein", "aus", "cache", "cache_quote", "ein_je_lauf"}


def _lauf(vor, model="coder-go", tokens=None, event="agent_run_finished"):
    return {"ts": (NOW - vor).astimezone().isoformat(), "event": event, "model": model,
            "exit_code": 0, "tokens": tokens}


def _t(ein, aus, cache):
    return {"input_tokens": ein, "output_tokens": aus, "cache_read_input_tokens": cache}


def test_summary_hat_genau_die_sieben_schluessel():
    s = st.token_summary([_lauf(H, tokens=_t(10, 1, 5))], NOW)
    assert set(s) == SCHLUESSEL | {"modelle"}
    je_modell = s["modelle"]["coder-go"]
    assert set(je_modell) == SCHLUESSEL
    assert (je_modell["ein"], je_modell["aus"], je_modell["cache"]) == (10, 1, 5)


def test_leere_liste_ist_null_ohne_basis():
    s = st.token_summary([], NOW)
    assert (s["laeufe"], s["mit_token"], s["ein"], s["aus"], s["cache"]) == (0, 0, 0, 0, 0)
    assert s["cache_quote"] is None and s["ein_je_lauf"] is None and s["modelle"] == {}


def test_lauf_ohne_tokens_zaehlt_nur_als_lauf():
    """OpenCode schreibt heute kein `tokens` (nur das Qwen-`result`-Event füllt LAST_AGENT_USAGE)."""
    s = st.token_summary([_lauf(H, model="opencode"), _lauf(2 * H, tokens=None)], NOW)
    assert (s["laeufe"], s["mit_token"]) == (2, 0)
    assert s["cache_quote"] is None and s["ein_je_lauf"] is None
    assert s["modelle"]["opencode"]["laeufe"] == 1 and s["modelle"]["opencode"]["mit_token"] == 0


@pytest.mark.parametrize("wert", [True, False, -1, -100, "7", None, 1.5, [1], {"a": 1}])
def test_unbrauchbare_einzelwerte_zaehlen_null(wert):
    s = st.token_summary([_lauf(H, tokens={"input_tokens": wert, "output_tokens": 5,
                                           "cache_read_input_tokens": wert})], NOW)
    assert s["mit_token"] == 1
    assert (s["ein"], s["aus"], s["cache"]) == (0, 5, 0)
    assert s["cache_quote"] is None and s["ein_je_lauf"] == 0


def test_nur_unbrauchbare_werte_sind_kein_brauchbarer_lauf():
    for tokens in (_t(True, "x", -5), {}, {"input_tokens": None}, "1000", 1000, ["1"]):
        s = st.token_summary([_lauf(H, tokens=tokens)], NOW)
        assert (s["laeufe"], s["mit_token"], s["ein"]) == (1, 0, 0), tokens


def test_fremde_events_kaputtes_ts_und_nicht_dicts_werden_ignoriert():
    ts = (NOW - H).astimezone().isoformat()
    events = [
        "keine Zeile", 42, None, ["agent_run_finished"],
        {"event": "agent_run_finished", "ts": "kaputt", "tokens": _t(1, 1, 1)},
        {"event": "agent_run_finished", "tokens": _t(1, 1, 1)},
        {"event": "agent_run_finished", "ts": 12345, "tokens": _t(1, 1, 1)},
        {"event": "run_finished", "ts": ts, "tokens": _t(1, 1, 1)},
        {"event": "review_posted", "ts": ts, "model": "coder-go"},
        _lauf(H, tokens=_t(1_000, 10, 200)),
    ]
    s = st.token_summary(events, NOW)
    assert (s["laeufe"], s["ein"], s["cache_quote"]) == (1, 1_000, 20)


def test_fenstergrenzen_sind_eingeschlossen():
    events = [
        _lauf(timedelta(0), tokens=_t(1, 0, 0)),            # genau jetzt
        _lauf(7 * 24 * H, tokens=_t(10, 0, 0)),             # genau am Fensteranfang
        _lauf(7 * 24 * H + timedelta(seconds=1), tokens=_t(100, 0, 0)),
        _lauf(-H, tokens=_t(1_000, 0, 0)),                  # liegt in der Zukunft
    ]
    s = st.token_summary(events, NOW)
    assert (s["laeufe"], s["ein"]) == (2, 11)


def test_modelle_werden_sortiert_und_unbekannt():
    events = [
        _lauf(H, model="coder-pro-go", tokens=_t(30, 3, 0)),
        _lauf(H, model="coder-go", tokens=_t(10, 1, 5)),
        _lauf(H, model=None, tokens=_t(7, 1, 0)),
        _lauf(H, model=42, tokens=_t(7, 1, 0)),
        _lauf(H, model="", tokens=_t(7, 1, 0)),
    ]
    m = st.token_summary(events, NOW)["modelle"]
    assert list(m) == ["coder-go", "coder-pro-go", "unbekannt"]
    assert (m["unbekannt"]["laeufe"], m["unbekannt"]["ein"]) == (3, 21)
    assert m["coder-go"]["ein_je_lauf"] == 10 and m["coder-pro-go"]["cache_quote"] == 0
    assert all("modelle" not in eintrag for eintrag in m.values())


def test_quoten_werden_gerundet():
    s = st.token_summary([_lauf(H, tokens=_t(3, 0, 2))], NOW)
    assert s["cache_quote"] == 67
    s = st.token_summary([_lauf(H, tokens=_t(10, 0, 0)) for _ in range(3)], NOW)
    assert s["ein_je_lauf"] == 10
    s = st.token_summary([_lauf(H, tokens=_t(10, 0, 0)), _lauf(H, tokens=_t(10, 0, 0)),
                          _lauf(H, tokens=_t(12, 0, 0))], NOW)
    assert s["ein_je_lauf"] == 11


def test_build_token_stats_trennt_24h_und_7d(tmp_path):
    log = tmp_path / "audit.jsonl"
    zeilen = [json.dumps(_lauf(2 * H, tokens=_t(1_000, 100, 250))),
              json.dumps(_lauf(30 * H, tokens=_t(3_000, 300, 0)))]
    log.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    out = st.build_token_stats(str(log), now=NOW)
    assert list(out) == ["24h", "7d"]
    assert out["24h"]["ein"] == 1_000 and out["24h"]["cache_quote"] == 25
    assert out["7d"]["ein"] == 4_000 and out["7d"]["laeufe"] == 2


def test_build_token_stats_ohne_datei_ist_leer(tmp_path):
    out = st.build_token_stats(str(tmp_path / "gibt-es-nicht.jsonl"), now=NOW)
    assert out["24h"]["laeufe"] == 0 and out["24h"]["cache_quote"] is None
    assert out["7d"]["modelle"] == {}


def test_build_token_stats_ueberspringt_kaputte_zeilen(tmp_path):
    log = tmp_path / "audit.jsonl"
    zeilen = [json.dumps(_lauf(H, tokens=_t(5, 1, 1))), "{kaputt", "[1, 2]", "null", "",
              json.dumps(_lauf(H, tokens=_t(5, 1, 1)))]
    log.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    out = st.build_token_stats(str(log), now=NOW)
    assert out["24h"]["laeufe"] == 2 and out["24h"]["ein"] == 10


def test_read_audit_events_liest_nur_die_letzten_bytes(tmp_path):
    log = tmp_path / "audit.jsonl"
    alt = json.dumps(_lauf(2 * H, tokens=_t(999, 9, 9)))
    neu = json.dumps(_lauf(H, tokens=_t(1, 1, 0)))
    log.write_text(alt + "\n" + neu + "\n", encoding="utf-8")
    assert len(st._read_audit_events(str(log))) == 2
    # Grenze mitten in der alten Zeile: der angeschnittene Rest ist kein JSON und fällt weg
    assert st._read_audit_events(str(log), max_bytes=len(neu) + 5) == [json.loads(neu)]
