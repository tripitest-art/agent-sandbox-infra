import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_issue_274", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_issue_274"] = w
_spec.loader.exec_module(w)

T0 = 1760880000.0
MIN = 60.0
PARKED = "pr269@2026-10-22T04:39:54Z"


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))


def _kollision(kandidat, halter):
    return {f"{w.KOLLISION}{kandidat}:{halter}": {"start": T0, "warned": False}}


def _alarme(monkeypatch):
    gesehen = []
    monkeypatch.setattr(w, "alarm", lambda skey, fehler: gesehen.append((skey, str(fehler))))
    return gesehen


def test_ursache_nennt_halter_sortiert_ohne_duplikate_und_geparkten_auftrag():
    state = {**_kollision(223, 221), **_kollision(224, 221), **_kollision(227, 220)}
    assert w.stillstand_ursache(state, PARKED) == f"belegt durch #220, #221; {PARKED} wartet auf Stephan"


def test_ursache_nur_halter_oder_nur_geparkt():
    assert w.stillstand_ursache(_kollision(223, 221), None) == "belegt durch #221"
    assert w.stillstand_ursache({}, PARKED) == f"{PARKED} wartet auf Stephan"


def test_ursache_ist_none_wenn_nichts_bestimmbar():
    assert w.stillstand_ursache({}, None) is None
    assert w.stillstand_ursache({"_stillstand": {"seit": T0}}, None) is None


def test_ursache_uebergeht_unlesbare_schluessel_ohne_fehler():
    state = {f"{w.KOLLISION}abc": 1, f"{w.KOLLISION}223:x": 1, f"{w.KOLLISION}": 1, **_kollision(223, 221)}
    assert w.stillstand_ursache(state, None) == "belegt durch #221"


def test_alarmzeile_nennt_die_ursache_zuerst(monkeypatch):
    gesehen = _alarme(monkeypatch)
    state = {w.STILLSTAND: {"seit": T0, "gemeldet": False}}
    gruende = ["#223: Grund A", "#224: Grund B", "#227: Grund C", "#271: Grund D"]
    assert w.stillstand_pruefen(state, gruende, now=T0 + 60 * MIN, ursache="belegt durch #221") is True
    assert gesehen == [("-", "Stillstand seit 60 Minuten: Ursache: belegt durch #221; "
                             "#223: Grund A; #224: Grund B; #227: Grund C; +1 weitere")]


def test_alarmzeile_ohne_ursache_bleibt_wie_bisher(monkeypatch):
    gesehen = _alarme(monkeypatch)
    state = {w.STILLSTAND: {"seit": T0, "gemeldet": False}}
    assert w.stillstand_pruefen(state, ["#223: Grund A", "#224: Grund B"], now=T0 + 60 * MIN) is True
    assert gesehen == [("-", "Stillstand seit 60 Minuten: #223: Grund A; #224: Grund B")]


def test_leere_ursache_zaehlt_als_keine(monkeypatch):
    gesehen = _alarme(monkeypatch)
    state = {w.STILLSTAND: {"seit": T0, "gemeldet": False}}
    w.stillstand_pruefen(state, ["#223: Grund A"], now=T0 + 60 * MIN, ursache="")
    assert gesehen == [("-", "Stillstand seit 60 Minuten: #223: Grund A")]


def test_meldung_bleibt_einmalig_auch_mit_ursache(monkeypatch):
    gesehen = _alarme(monkeypatch)
    state = {w.STILLSTAND: {"seit": T0, "gemeldet": False}}
    assert w.stillstand_pruefen(state, ["#223: A"], now=T0 + 60 * MIN, ursache="belegt durch #221") is True
    assert w.stillstand_pruefen(state, ["#223: A"], now=T0 + 90 * MIN, ursache="belegt durch #221") is False
    assert len(gesehen) == 1


def test_ursache_ueberlebt_die_laengenkuerzung_von_alarm(capsys):
    state = {w.STILLSTAND: {"seit": T0, "gemeldet": False}}
    lang = "berührt sandbox/worker_tick.py, während bereits ein Auftrag an sandbox/worker_tick.py in Arbeit ist"
    gruende = [f"#{n}: {lang}" for n in (223, 224, 227, 271)]
    w.stillstand_pruefen(state, gruende, now=T0 + 60 * MIN, ursache=f"belegt durch #221; {PARKED} wartet auf Stephan")
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1
    assert "Ursache: belegt durch #221" in zeilen[0]
    assert len(zeilen[0]) <= 320
