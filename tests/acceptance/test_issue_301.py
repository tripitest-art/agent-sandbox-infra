"""Akzeptanztests für Issue #301: Zweiter Worker-Slot für einfache Tasks (Kosten sparen).

Der Takt bekommt einen Billig-Slot (WORKER_SLOT=2), der ein kostenloses LiteLLM-Modell
nutzt und nur Issues mit dem Label `worker:billig` übernimmt. Damit spart der Hauptslot
Token-Kosten für einfache Aufgaben.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"


def _load(monkeypatch, name="worker_tick_billig_slot"):
    monkeypatch.delenv("LITELLM_FALLBACKS", raising=False)
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    spec = importlib.util.spec_from_file_location(name, _PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_neuer_kostenfreier_alias_hat_anzeigenamen(monkeypatch):
    """Jeder kostenlose Worker-Alias braucht einen sinnvollen Namen in WORKER_NAMES."""
    w = _load(monkeypatch)
    freie = [a for a in w.WORKER_NAMES if a not in w.BEZAHLTE_MODELLE]
    assert freie, "Mindestens ein kostenloses Worker-Modell muss konfiguriert sein"
    for alias in freie:
        name = w.worker_name(alias)
        assert name != alias, f"Alias {alias!r} braucht einen Anzeigenamen in WORKER_NAMES"


def test_billig_label_konstante_vorhanden(monkeypatch):
    """Das Label `worker:billig` ist als Konstante definiert."""
    w = _load(monkeypatch)
    assert hasattr(w, "BILLIG_LABEL")
    assert w.BILLIG_LABEL == "worker:billig"


def test_issue_mit_billig_label_ist_billig(monkeypatch):
    """Ein Issue mit Label worker:billig wird als Billig-Slot-Kandidat erkannt."""
    w = _load(monkeypatch)
    assert w.is_billig_issue({"labels": [{"name": "worker:billig"}]}) is True


def test_issue_ohne_billig_label_nicht_billig(monkeypatch):
    """Ein Issue ohne worker:billig ist kein Kandidat für den Billig-Slot."""
    w = _load(monkeypatch)
    assert w.is_billig_issue({"labels": [{"name": "prio:niedrig"}]}) is False
    assert w.is_billig_issue({"labels": []}) is False
    assert w.is_billig_issue({"labels": [{"name": "prio:kritisch"}]}) is False


def test_kostenlimit_pruefung_vorhanden(monkeypatch):
    """Es gibt eine Prüfung, ob das Tages-/Laufkosten-Limit überschritten ist."""
    w = _load(monkeypatch)
    assert hasattr(w, "cost_limit_exceeded")
    assert w.cost_limit_exceeded(None, 1.0) is False
    assert w.cost_limit_exceeded(2.0, 1.0) is True


def test_kostenlimit_schreibt_alarm(monkeypatch, tmp_path, capsys):
    """Bei überschrittenem Limit wird eine ⚠ Stephan:-Zeile geschrieben."""
    w = _load(monkeypatch)
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    alarmiert = []

    def fake_alarm(skey, error):
        alarmiert.append((skey, str(error)))

    monkeypatch.setattr(w, "alarm", fake_alarm)
    w.cost_limit_exceeded(5.0, 1.0)
    assert any("Limit" in str(e) for _, e in alarmiert), "Alarm bei Kostenüberschreitung fehlt"
