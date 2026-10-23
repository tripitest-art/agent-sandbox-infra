import importlib.util
import sys
from datetime import datetime
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "dashboard.py"
_spec = importlib.util.spec_from_file_location("dashboard_warteschlange", _PATH)
d = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_warteschlange"] = d
_spec.loader.exec_module(d)

NOW = datetime(2026, 10, 23, 12, 0, 0)
GRUND = "berührt sandbox/worker_tick.py, während bereits ein Auftrag an sandbox/worker_tick.py in Arbeit ist (Regel 8)."


def _zeile(slot, text):
    return f"2026-10-23 12:24:17 [{slot}] {text}"


def test_uebersprungenes_issue_kommt_in_die_warteschlange():
    lines = [_zeile("w2", f"Issue #282 übersprungen: {GRUND}")]
    assert d.parse_uebersprungen(lines) == [{"issue": 282, "grund": GRUND}]


def test_je_issue_zaehlt_der_letzte_grund_und_die_reihenfolge_ist_aufsteigend():
    lines = [
        _zeile("w1", "Issue #282 übersprungen: alter Grund"),
        _zeile("w1", "Issue #281 übersprungen: Grund A"),
        _zeile("w2", "Issue #282 übersprungen: neuer Grund   "),
        _zeile("w1", "Keine Arbeit."),
    ]
    assert d.parse_uebersprungen(lines) == [
        {"issue": 281, "grund": "Grund A"},
        {"issue": 282, "grund": "neuer Grund"},
    ]


def test_uebernommenes_issue_verlaesst_die_warteschlange():
    lines = [
        _zeile("w1", "Issue #281 übersprungen: Grund A"),
        _zeile("w1", "Issue #282 übersprungen: Grund B"),
        _zeile("w1", "Issue #281 erfolgreich auf 'in-arbeit' gesetzt."),
    ]
    assert d.parse_uebersprungen(lines) == [{"issue": 282, "grund": "Grund B"}]


def test_leere_eingabe_und_fremde_zeilen_ergeben_nichts():
    assert d.parse_uebersprungen([]) == []
    assert d.parse_uebersprungen([_zeile("w1", "Issue 281 übersprungen"), "Müll"]) == []


def test_build_state_traegt_die_warteschlange():
    eintraege = [{"issue": 282, "grund": GRUND}]
    state = d.build_state({}, None, False, [], NOW, None, eintraege)
    assert state["warteschlange"] == eintraege
    assert state["kopf"]["wartend"] == 1


def test_build_state_ohne_warteschlange_bleibt_leer():
    state = d.build_state({}, None, False, [], NOW)
    assert state["warteschlange"] == []
    assert state["kopf"]["wartend"] == 0


def test_dashboard_state_liest_die_warteschlange_aus_dem_log(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text(
        _zeile("w2", f"Issue #282 übersprungen: {GRUND}") + "\n"
        + _zeile("w2", "Keine Arbeit.") + "\n",
        encoding="utf-8",
    )
    dash = d.Dashboard(
        d.StatusCache(lambda: {"zeit": "2026-10-23T12:00:00+00:00", "projekte": []}, ttl=60),
        d.LogBuffer(str(log)),
        slot_dir=str(tmp_path / "slots"),
        infra_dir=str(tmp_path),
        is_pid_alive=lambda pid: True,
        clock=lambda: NOW,
    )
    state = dash.state()
    assert state["warteschlange"] == [{"issue": 282, "grund": GRUND}]
    assert state["kopf"]["wartend"] == 1
