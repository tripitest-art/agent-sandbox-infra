"""Akzeptanztests für Issue #<N>: Regel 7 – kritische Fehler als „⚠ Stephan:“-Zeile."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("worker_tick", ROOT / "sandbox" / "worker_tick.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)


def test_alarm_zeile_hat_feste_form(capsys):
    alarm = getattr(w, "alarm", None)
    assert alarm is not None, "worker_tick.alarm fehlt"
    alarm("issue7", ValueError("kaputt"))
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].endswith("⚠ Stephan: issue7: ValueError: kaputt")


def test_alarm_bleibt_einzeilig_und_kurz(capsys):
    alarm = getattr(w, "alarm", None)
    assert alarm is not None, "worker_tick.alarm fehlt"
    alarm("issue7", RuntimeError("Anfang\nzweite Zeile " + "x" * 500))
    out = capsys.readouterr().out.strip()
    assert out.count("⚠ Stephan:") == 1
    assert "\n" not in out
    assert len(out) <= 320


def test_main_meldet_absturz_der_auftragsausfuehrung(monkeypatch, capsys):
    def boom():
        raise RuntimeError("Arbeitsverzeichnis kaputt")

    monkeypatch.setattr(w, "load_state", lambda: {})
    monkeypatch.setattr(w, "save_state", lambda state: None)
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "pick_job", lambda state: ("issue7", "Auftrag", None, None))
    monkeypatch.setattr(w, "choose_model", lambda: ("coder", []))
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
    monkeypatch.setattr(w, "prepare_workdir", boom)
    try:
        rc = w.main()
    except Exception as exc:      # heute schlägt der Fehler durch; geprüft wird die Logzeile
        rc = f"Absturz {type(exc).__name__}"
    out = capsys.readouterr().out
    assert rc == 0
    assert "⚠ Stephan: issue7" in out


def test_main_meldet_kaputten_zustand(monkeypatch, capsys):
    def boom():
        raise OSError("Zustandsdatei nicht lesbar")

    monkeypatch.setattr(w, "load_state", boom)
    try:
        rc = w.main()
    except Exception as exc:
        rc = f"Absturz {type(exc).__name__}"
    out = capsys.readouterr().out
    assert rc == 0
    assert "⚠ Stephan:" in out
