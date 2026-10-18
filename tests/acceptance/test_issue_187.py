import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)


def _state():
    return {
        "pr277@abc": 2, "_park:pr277@abc": "geparkt",
        "issue14": 2, "_park:issue14": "geparkt",
        "erledigt": ["x"],
    }


def test_prune_entfernt_marke_und_zaehler_geschlossener_auftraege():
    state = _state()
    entfernt = w.prune_parked(state, lambda skey: skey != "pr277@abc")
    assert entfernt == ["pr277@abc"]
    assert "_park:pr277@abc" not in state and "pr277@abc" not in state
    assert state["_park:issue14"] == "geparkt" and state["issue14"] == 2
    assert state["erledigt"] == ["x"]


def test_prune_laesst_offene_auftraege_unberuehrt():
    state = _state()
    assert w.prune_parked(state, lambda skey: True) == []
    assert state == _state()


def test_prune_behaelt_marke_wenn_pruefung_wirft():
    state = _state()

    def kaputt(skey):
        raise RuntimeError("gh nicht erreichbar")

    assert w.prune_parked(state, kaputt) == []
    assert state == _state()


def test_parked_job_meldet_nach_prune_nichts_mehr():
    state = {"_park:pr277@abc": "geparkt", "pr277@abc": 2}
    assert w.parked_job(state) == "pr277@abc"
    w.prune_parked(state, lambda skey: False)
    assert w.parked_job(state) is None


def test_idle_message_ohne_alles():
    assert w.idle_message(None, []) == "Warte auf nächsten Takt"


def test_idle_message_nur_geparkt():
    assert w.idle_message("pr277@abc", []) == "pr277@abc: wartet auf Stephan"


def test_idle_message_nennt_uebersprungene():
    msg = w.idle_message(None, ["#329: Datei-Kollision auf sc_digger/bot.py", "#349: berührt main.py"])
    assert msg == "Übersprungen (2): #329: Datei-Kollision auf sc_digger/bot.py; #349: berührt main.py"


def test_idle_message_kuerzt_auf_drei():
    msg = w.idle_message(None, [f"#{i}: x" for i in range(1, 6)])
    assert msg == "Übersprungen (5): #1: x; #2: x; #3: x; +2 weitere"


def test_idle_message_geparkt_und_uebersprungen():
    msg = w.idle_message("pr9@abc", ["#1: x"])
    assert msg == "Übersprungen (1): #1: x | pr9@abc: wartet auf Stephan"


def test_prune_ohne_marken_ist_leer():
    state = {"erledigt": []}
    assert w.prune_parked(state, lambda skey: False) == []
    assert state == {"erledigt": []}
