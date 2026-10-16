import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

# Gleiche Form wie das echte Ergebnis-Ereignis von Qwen Code (stream-json); Schlüsselteile sind erfunden.
FEHLER = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                     "duration_ms": 284, "num_turns": 0,
                     "error": {"message": "[API Error: 429 Budget has been exceeded! Key=worker (sk-...abcd) "
                                          "Current cost: 5.0, Max budget: 5.0]"}})
ERFOLG = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                     "duration_ms": 5000, "num_turns": 3, "result": "fertig"})
ANDERER_FEHLER = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True,
                             "duration_ms": 9000, "num_turns": 50, "error": {"message": "Max turns reached"}})
FEHLER_TEXT = "API 429: Budget has been exceeded!"


def _lauf(monkeypatch, tmp_path, zeilen, rc=1, key="issue7"):
    """Richtet main() ein; der Agent wird durch das Einspielen der Stream-Zeilen ersetzt."""
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "PREFIX", "")
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "pick_job", lambda state: (key, "Auftrag", None, None))
    monkeypatch.setattr(w, "choose_model", lambda: ("coder", []))
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "slot_status", lambda *a, **k: None)
    geprueft = []
    monkeypatch.setattr(w, "check_issue_result", lambda k: geprueft.append(k))

    def run(prompt):
        for zeile in zeilen:
            w.log_stream_line(zeile)
        return rc

    monkeypatch.setattr(w, "run_agent", run)
    return geprueft


def _versuche(tmp_path, key="issue7"):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8")).get(key, 0)


def test_short_api_error_nennt_status_und_grund_ohne_schluessel():
    assert w.short_api_error("[API Error: 429 Budget has been exceeded! Key=worker (sk-...abcd) "
                             "Current cost: 5.0, Max budget: 5.0]") == FEHLER_TEXT
    assert w.short_api_error("[API Error: 401 Authentication Error, Invalid proxy server token passed. "
                             "Received API Key = sk-...test, Key Hash (Token) =b826...1302. Unable]") == \
        "API 401: Authentication Error, Invalid proxy server token passed."
    lang = w.short_api_error("[API Error: 500 " + "x" * 400 + "]")
    assert lang.startswith("API 500: ") and len(lang) <= 160 and "\n" not in lang


def test_log_stream_line_merkt_den_anbieterfehler(monkeypatch, capsys):
    monkeypatch.setattr(w, "LAST_AGENT_ERROR", None, raising=False)
    w.log_stream_line(FEHLER)
    assert w.LAST_AGENT_ERROR == FEHLER_TEXT
    out = capsys.readouterr().out
    assert FEHLER_TEXT in out and "sk-" not in out and "abcd" not in out


def test_log_stream_line_erfolg_und_fremder_fehler_sind_kein_anbieterfehler(monkeypatch):
    monkeypatch.setattr(w, "LAST_AGENT_ERROR", None, raising=False)
    w.log_stream_line(ERFOLG)
    w.log_stream_line(ANDERER_FEHLER)
    assert w.LAST_AGENT_ERROR is None


def test_main_anbieterfehler_verbraucht_keinen_versuch(monkeypatch, tmp_path, capsys):
    geprueft = _lauf(monkeypatch, tmp_path, [FEHLER])
    assert w.main() == 0
    out = capsys.readouterr().out
    assert f"issue7: Anbieterfehler im Lauf ({FEHLER_TEXT}), Versuch zählt nicht." in out
    assert _versuche(tmp_path) == 0
    assert geprueft == []            # kein „kein PR“-Befund für einen Lauf, der nie stattfand


def test_main_meldet_denselben_anbieterfehler_nur_einmal_an_stephan(monkeypatch, tmp_path, capsys):
    _lauf(monkeypatch, tmp_path, [FEHLER])
    assert w.main() == 0
    assert w.main() == 0
    out = capsys.readouterr().out
    assert out.count("⚠ Stephan: issue7: Fehler: Anbieterfehler: " + FEHLER_TEXT) == 1
    assert out.count("Versuch zählt nicht.") == 2
    assert _versuche(tmp_path) == 0


def test_main_gewoehnlicher_fehlschlag_zaehlt_weiter(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(w, "LAST_AGENT_ERROR", "alt", raising=False)   # Rest eines früheren Laufs
    geprueft = _lauf(monkeypatch, tmp_path, [ANDERER_FEHLER])
    assert w.main() == 0
    assert _versuche(tmp_path) == 1
    assert geprueft == ["issue7"]
    assert "Anbieterfehler" not in capsys.readouterr().out
