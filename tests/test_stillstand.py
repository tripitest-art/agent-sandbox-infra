"""Tests für den Stillstandsalarm aus main() (#218), ohne Netzwerk."""

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_stillstand_main", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_stillstand_main"] = w
_spec.loader.exec_module(w)

T0 = 1760880000.0
MIN = 60.0
GRUND = "#364: Datei-Kollision mit aktivem Auftrag auf sc_digger/db.py"


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    monkeypatch.setattr(w, "PREFIX", "")
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "verwaiste_melden", lambda state, now=None: [])
    monkeypatch.setattr(w, "prune_parked", lambda state, still_open: [])
    monkeypatch.setattr(w, "entparken", lambda state, bereit_seit: [])
    monkeypatch.setattr(w, "slot_status", lambda *a, **k: None)


def test_main_meldet_stillstand_mit_gruenden_aus_skipped_und_park(monkeypatch, capsys):
    """#218: Im Zweig „keine Arbeit“ zählen übersprungene Kandidaten und der geparkte Auftrag."""
    state = {"_park:issue7": True, "_stillstand": {"seit": T0, "gemeldet": False}}
    monkeypatch.setattr(w, "load_state", lambda: state)
    monkeypatch.setattr(w, "SKIPPED", [GRUND])
    monkeypatch.setattr(w, "pick_job", lambda s: None)
    monkeypatch.setattr(w, "jetzt_s", lambda: T0 + 60 * MIN)

    assert w.main() == 0

    zeilen = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1
    assert "Stillstand seit 60 Minuten" in zeilen[0]
    assert "#364" in zeilen[0]
    assert "issue7: geparkt (wartet auf Stephan)" in zeilen[0]
    assert state["_stillstand"]["gemeldet"] is True


def test_main_entfernt_den_eintrag_wenn_nichts_mehr_wartet(monkeypatch):
    """#218: Ohne Übersprungene und ohne geparkten Auftrag endet der Stillstand still."""
    state = {"_stillstand": {"seit": T0, "gemeldet": True}}
    monkeypatch.setattr(w, "load_state", lambda: state)
    monkeypatch.setattr(w, "SKIPPED", [])
    monkeypatch.setattr(w, "pick_job", lambda s: None)

    assert w.main() == 0
    assert "_stillstand" not in state


def test_main_nennt_die_kollision_als_ursache(monkeypatch, capsys):
    """#274: Die Ursache steht vor den Gründen, damit die Dreier-Grenze sie nicht verdrängt."""
    state = {"_stillstand": {"seit": T0, "gemeldet": False},
             f"{w.KOLLISION}223:221": {"start": T0, "warned": False},
             f"{w.KOLLISION}224:221": {"start": T0, "warned": False}}
    monkeypatch.setattr(w, "load_state", lambda: state)
    monkeypatch.setattr(w, "SKIPPED", [GRUND])
    monkeypatch.setattr(w, "pick_job", lambda s: None)
    monkeypatch.setattr(w, "jetzt_s", lambda: T0 + 60 * MIN)

    assert w.main() == 0

    zeilen = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1
    assert "Minuten: Ursache: belegt durch #221; " in zeilen[0]
    assert GRUND in zeilen[0]


def test_main_nennt_den_geparkten_auftrag_als_ursache(monkeypatch, capsys):
    """#274: Ein geparkter Auftrag ist selbst die Ursache, auch ohne Datei-Kollision."""
    state = {"_stillstand": {"seit": T0, "gemeldet": False},
             "_park:pr269@2026-10-22T04:39:54Z": True}
    monkeypatch.setattr(w, "load_state", lambda: state)
    monkeypatch.setattr(w, "SKIPPED", [GRUND])
    monkeypatch.setattr(w, "pick_job", lambda s: None)
    monkeypatch.setattr(w, "jetzt_s", lambda: T0 + 60 * MIN)

    assert w.main() == 0

    zeilen = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1
    assert "Ursache: pr269@2026-10-22T04:39:54Z wartet auf Stephan; " in zeilen[0]


def test_main_ohne_bestimmbare_ursache_bleibt_die_alarmzeile_gleich(monkeypatch, capsys):
    """#274: Ohne Halter und ohne geparkten Auftrag kommt kein „Ursache:“ in die Zeile."""
    state = {"_stillstand": {"seit": T0, "gemeldet": False}}
    monkeypatch.setattr(w, "load_state", lambda: state)
    monkeypatch.setattr(w, "SKIPPED", [GRUND])
    monkeypatch.setattr(w, "pick_job", lambda s: None)
    monkeypatch.setattr(w, "jetzt_s", lambda: T0 + 60 * MIN)

    assert w.main() == 0

    zeilen = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1
    assert "Minuten: " + GRUND in zeilen[0]
    assert "Ursache" not in zeilen[0]


def test_main_raeumt_den_eintrag_sobald_ein_auftrag_laeuft(monkeypatch):
    """#218: Findet main wieder Arbeit, verschwindet der Stillstand aus dem Zustand."""
    state = {"_stillstand": {"seit": T0, "gemeldet": True}}
    monkeypatch.setattr(w, "load_state", lambda: state)
    monkeypatch.setattr(w, "pick_job", lambda s: ("issue7", "Auftrag", None, None))
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "choose_model", lambda: ("coder", []))
    monkeypatch.setattr(w, "claim_job", lambda skey: False)

    assert w.main() == 0
    assert "_stillstand" not in state
