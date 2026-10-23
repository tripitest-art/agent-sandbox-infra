import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "dashboard_stats.py"
_spec = importlib.util.spec_from_file_location("dashboard_stats_token", _PATH)
st = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_stats_token"] = st
_spec.loader.exec_module(st)

NOW = datetime(2026, 10, 23, 12, 0, 0)
H = timedelta(hours=1)


def _lauf(vor, model="coder-go", tokens=None, event="agent_run_finished"):
    ts = (NOW - vor).astimezone().isoformat()
    return {"ts": ts, "event": event, "model": model, "exit_code": 0, "tokens": tokens}


def _t(ein, aus, cache):
    return {"input_tokens": ein, "output_tokens": aus, "cache_read_input_tokens": cache}


def test_summen_quote_und_mittel_im_fenster():
    events = [
        _lauf(2 * H, tokens=_t(100_000, 2_000, 80_000)),
        _lauf(3 * H, tokens=_t(50_000, 1_000, 10_000)),
        _lauf(4 * H, tokens=None),                         # z. B. OpenCode: keine Usage
        _lauf(8 * 24 * H, tokens=_t(999, 9, 9)),           # außerhalb von 7 Tagen
        _lauf(1 * H, event="run_finished", tokens=_t(5, 5, 5)),
    ]
    s = st.token_summary(events, NOW)
    assert s["laeufe"] == 3 and s["mit_token"] == 2
    assert (s["ein"], s["aus"], s["cache"]) == (150_000, 3_000, 90_000)
    assert s["cache_quote"] == 60
    assert s["ein_je_lauf"] == 75_000


def test_je_modell_getrennt_und_unbekannt():
    events = [
        _lauf(H, model="coder-go", tokens=_t(1_000, 10, 500)),
        _lauf(H, model="coder-pro-go", tokens=_t(3_000, 30, 0)),
        _lauf(H, model="", tokens=_t(10, 1, 0)),
    ]
    m = st.token_summary(events, NOW)["modelle"]
    assert list(m) == sorted(m)
    assert m["coder-go"]["cache_quote"] == 50
    assert m["coder-pro-go"]["ein"] == 3_000 and m["coder-pro-go"]["cache_quote"] == 0
    assert m["unbekannt"]["laeufe"] == 1
    assert "modelle" not in m["coder-go"]


def test_ohne_basis_none_und_kaputte_werte_zaehlen_null():
    leer = st.token_summary([], NOW)
    assert leer["laeufe"] == 0 and leer["cache_quote"] is None and leer["ein_je_lauf"] is None
    assert leer["modelle"] == {}
    events = [
        _lauf(H, tokens={"input_tokens": True, "output_tokens": -5, "cache_read_input_tokens": "7"}),
        _lauf(H, tokens={"input_tokens": 400, "output_tokens": "x"}),
        "keine Zeile", {"event": "agent_run_finished", "ts": "kaputt", "tokens": _t(1, 1, 1)},
    ]
    s = st.token_summary(events, NOW)
    assert s["laeufe"] == 2 and s["mit_token"] == 1
    assert (s["ein"], s["aus"], s["cache"]) == (400, 0, 0)


def test_24h_fenster_und_grenze():
    events = [_lauf(24 * H, tokens=_t(10, 1, 5)), _lauf(25 * H, tokens=_t(20, 2, 0))]
    assert st.token_summary(events, NOW, st.WINDOWS["24h"])["ein"] == 10
    assert st.token_summary(events, NOW)["ein"] == 30


def test_build_token_stats_liest_datei_und_ueberspringt_muell(tmp_path):
    log = tmp_path / "audit.jsonl"
    zeilen = [json.dumps(_lauf(2 * H, tokens=_t(1_000, 100, 250))), "{kaputt", "[1, 2]",
              json.dumps(_lauf(30 * H, tokens=_t(3_000, 300, 0)))]
    log.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    out = st.build_token_stats(str(log), now=NOW)
    assert out["24h"]["ein"] == 1_000 and out["24h"]["cache_quote"] == 25
    assert out["7d"]["ein"] == 4_000 and out["7d"]["laeufe"] == 2
    fehlt = st.build_token_stats(str(tmp_path / "gibt-es-nicht.jsonl"), now=NOW)
    assert fehlt["7d"]["laeufe"] == 0 and fehlt["24h"]["cache_quote"] is None
