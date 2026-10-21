"""Akzeptanztest: Ein Stillstand der Pipeline wird nach einer Stunde einmal als „⚠ Stephan:“ gemeldet."""
import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_stillstand", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_stillstand"] = w
_spec.loader.exec_module(w)

T0 = 1760880000.0  # feste Epochen-Sekunden
MIN = 60.0
GRUND = "#364: Datei-Kollision mit aktivem Auftrag auf sc_digger/db.py"


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    monkeypatch.setattr(w, "PREFIX", "")


def test_schwelle_ist_eine_stunde():
    assert w.STILLSTAND_MINUTEN == 60


def test_stillstand_beginnt_mit_dem_ersten_grund(capsys):
    state = {}
    assert w.stillstand_pruefen(state, [GRUND], now=T0) is False
    assert state["_stillstand"]["seit"] == T0
    assert state["_stillstand"]["gemeldet"] is False
    assert "⚠ Stephan:" not in capsys.readouterr().out


def test_unter_der_schwelle_keine_meldung(capsys):
    state = {}
    w.stillstand_pruefen(state, [GRUND], now=T0)
    assert w.stillstand_pruefen(state, [GRUND], now=T0 + 59 * MIN) is False
    assert "⚠ Stephan:" not in capsys.readouterr().out


def test_ab_der_schwelle_genau_eine_meldung_mit_den_gruenden(capsys):
    state = {}
    w.stillstand_pruefen(state, [GRUND], now=T0)
    assert w.stillstand_pruefen(state, [GRUND], now=T0 + 60 * MIN) is True
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1
    assert "Stillstand" in zeilen[0] and "60" in zeilen[0] and "#364" in zeilen[0]
    assert state["_stillstand"]["gemeldet"] is True

    assert w.stillstand_pruefen(state, [GRUND], now=T0 + 61 * MIN) is False
    assert w.stillstand_pruefen(state, [GRUND], now=T0 + 300 * MIN) is False
    assert "⚠ Stephan:" not in capsys.readouterr().out


def test_ohne_gruende_endet_der_stillstand_und_ein_neuer_meldet_wieder(capsys):
    state = {"_stillstand": {"seit": T0, "gemeldet": True}, "andere": 1}
    assert w.stillstand_pruefen(state, [], now=T0 + 100 * MIN) is False
    assert "_stillstand" not in state and state["andere"] == 1

    assert w.stillstand_pruefen(state, [GRUND], now=T0 + 400 * MIN) is False
    assert state["_stillstand"]["seit"] == T0 + 400 * MIN
    assert w.stillstand_pruefen(state, [GRUND], now=T0 + 460 * MIN) is True
    assert capsys.readouterr().out.count("⚠ Stephan:") == 1


def test_die_meldung_nennt_hoechstens_drei_gruende(capsys):
    gruende = [f"#{n}: Datei-Kollision" for n in range(1, 6)]
    state = {"_stillstand": {"seit": T0, "gemeldet": False}}
    assert w.stillstand_pruefen(state, gruende, now=T0 + 60 * MIN) is True
    ausgabe = capsys.readouterr().out
    zeilen = [z for z in ausgabe.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1
    assert "#1:" in zeilen[0] and "#3:" in zeilen[0]
    assert "#4:" not in zeilen[0]
    assert "+2 weitere" in zeilen[0]


def test_nur_der_erste_slot_prueft(monkeypatch, capsys):
    monkeypatch.setattr(w, "WORKER_SLOT", "2")
    state = {"_stillstand": {"seit": T0, "gemeldet": False}}
    assert w.stillstand_pruefen(state, [GRUND], now=T0 + 120 * MIN) is False
    assert state == {"_stillstand": {"seit": T0, "gemeldet": False}}
    assert "⚠ Stephan:" not in capsys.readouterr().out


def test_unbrauchbarer_eintrag_im_zustand_startet_neu(capsys):
    state = {"_stillstand": "kaputt"}
    assert w.stillstand_pruefen(state, [GRUND], now=T0) is False
    assert state["_stillstand"]["seit"] == T0
