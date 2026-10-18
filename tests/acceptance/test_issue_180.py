import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

T0 = 1_800_000_000.0
MIN = 60.0
GRUND = "LiteLLM nicht erreichbar"


@pytest.fixture
def zeilen(monkeypatch, tmp_path):
    """Fängt log() ab; Zustand, Belegungen und Slot liegen in tmp_path."""
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    out: list[str] = []
    monkeypatch.setattr(w, "log", out.append)
    return out


def _alarme(zeilen):
    return [z for z in zeilen if "⚠ Stephan:" in z]


def test_schwelle_ist_zwanzig_minuten():
    assert w.LITELLM_ALARM_S == 1200


def test_kurzer_ausfall_meldet_nichts_und_merkt_den_beginn(zeilen):
    state: dict = {}
    assert w.litellm_down(state, now=T0) is False
    assert w.litellm_down(state, now=T0 + 19 * MIN) is False
    assert _alarme(zeilen) == []
    assert json.loads(Path(w.STATE).read_text(encoding="utf-8")) == state     # gespeichert
    assert len(state) >= 1


def test_ab_der_schwelle_genau_eine_zeile(zeilen):
    state: dict = {}
    w.litellm_down(state, now=T0)
    assert w.litellm_down(state, now=T0 + 20 * MIN - 1) is False
    assert w.litellm_down(state, now=T0 + 20 * MIN) is True
    assert w.litellm_down(state, now=T0 + 21 * MIN) is False
    assert w.litellm_down(state, now=T0 + 600 * MIN) is False
    alarme = _alarme(zeilen)
    assert len(alarme) == 1
    assert "LiteLLM seit 20 Minuten nicht erreichbar" in alarme[0]
    assert "\n" not in alarme[0]


def test_der_beginn_bleibt_beim_ersten_fehlschlag(zeilen):
    state: dict = {}
    w.litellm_down(state, now=T0)
    w.litellm_down(state, now=T0 + 10 * MIN)
    w.litellm_down(state, now=T0 + 25 * MIN)
    alarme = _alarme(zeilen)
    assert len(alarme) == 1 and "seit 25 Minuten" in alarme[0]               # gezählt ab T0, nicht ab dem letzten Takt


def test_ohne_ausfall_tut_litellm_up_nichts(zeilen):
    state: dict = {}
    assert w.litellm_up(state, now=T0) is False
    assert zeilen == [] and state == {}
    assert not Path(w.STATE).exists()                                         # nicht ohne Not speichern


def test_rueckkehr_nach_kurzem_ausfall_ist_still_und_raeumt_auf(zeilen):
    state: dict = {}
    w.litellm_down(state, now=T0)
    assert w.litellm_up(state, now=T0 + 5 * MIN) is False
    assert zeilen == [] and state == {}
    assert json.loads(Path(w.STATE).read_text(encoding="utf-8")) == {}


def test_rueckkehr_nach_alarm_wird_gemeldet_und_ein_neuer_ausfall_alarmiert_wieder(zeilen):
    state: dict = {}
    w.litellm_down(state, now=T0)
    w.litellm_down(state, now=T0 + 20 * MIN)
    assert w.litellm_up(state, now=T0 + 25 * MIN) is True
    assert zeilen[-1].endswith("LiteLLM wieder erreichbar nach 25 Minuten.")
    assert "⚠" not in zeilen[-1]                                               # Entwarnung ist kein Alarm
    assert state == {}
    w.litellm_down(state, now=T0 + 100 * MIN)
    w.litellm_down(state, now=T0 + 121 * MIN)
    assert len(_alarme(zeilen)) == 2


def test_nur_der_erste_slot_meldet(zeilen, monkeypatch):
    monkeypatch.setattr(w, "WORKER_SLOT", "2")
    state: dict = {}
    assert w.litellm_down(state, now=T0) is False
    assert w.litellm_down(state, now=T0 + 60 * MIN) is False
    assert zeilen == [] and state == {}
    monkeypatch.setattr(w, "WORKER_SLOT", "")                                  # ohne Slot-Nummer gilt der einzige Worker
    w.litellm_down(state, now=T0)
    assert w.litellm_down(state, now=T0 + 20 * MIN) is True


def _takt(monkeypatch, antworten, uhr):
    monkeypatch.setattr(w, "PREFIX", "")
    monkeypatch.setattr(w, "BACKEND", "litellm")
    folge = iter(antworten)
    monkeypatch.setattr(w, "litellm_ready", lambda: next(folge))
    monkeypatch.setattr(w, "jetzt_s", lambda: uhr[0])
    monkeypatch.setattr(w, "slot_status", lambda *a, **k: None)
    gewaehlt: list[int] = []
    monkeypatch.setattr(w, "pick_job", lambda state: gewaehlt.append(1) or None)
    return gewaehlt


def test_main_meldet_den_ausfall_einmal_und_die_rueckkehr(monkeypatch, zeilen):
    uhr = [T0]
    gewaehlt = _takt(monkeypatch, [GRUND, GRUND, GRUND, GRUND, None], uhr)
    for minuten in (0, 10, 21, 30):
        uhr[0] = T0 + minuten * MIN
        assert w.main() == 0
    assert gewaehlt == []                                                      # solange der Proxy fehlt, wird nichts gewählt
    assert len(_alarme(zeilen)) == 1 and "seit 21 Minuten" in _alarme(zeilen)[0]
    assert sum(GRUND + ", warte." in z for z in zeilen) == 1                    # bisheriges Verhalten bleibt
    uhr[0] = T0 + 35 * MIN
    assert w.main() == 0
    assert any(z.endswith("LiteLLM wieder erreichbar nach 35 Minuten.") for z in zeilen)
    assert gewaehlt == [1]                                                     # danach läuft der Takt normal weiter
    state = json.loads(Path(w.STATE).read_text(encoding="utf-8"))
    assert not [k for k in state if "litellm" in k.lower() and k != "_pc"]     # kein Ausfallrest im Zustand


def test_main_ohne_litellm_backend_fasst_den_ausfallzustand_nicht_an(monkeypatch, zeilen):
    monkeypatch.setattr(w, "BACKEND", "ollama")
    monkeypatch.setattr(w, "WAKE_PC", True)
    monkeypatch.setattr(w, "pick_job", lambda state: None)
    monkeypatch.setattr(w, "slot_status", lambda *a, **k: None)
    monkeypatch.setattr(w, "litellm_down", lambda *a, **k: pytest.fail("litellm_down ohne LiteLLM-Backend"))
    monkeypatch.setattr(w, "litellm_up", lambda *a, **k: pytest.fail("litellm_up ohne LiteLLM-Backend"))
    assert w.main() == 0
