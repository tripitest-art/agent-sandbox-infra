import calendar
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

T0 = float(calendar.timegm((2026, 10, 18, 16, 0, 0)))
MIN = 60.0


def iso(sekunden: float) -> str:
    return datetime.fromtimestamp(sekunden, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue(nummer: int, alter_min: float) -> dict:
    return {"number": nummer, "updatedAt": iso(T0 - alter_min * MIN)}


@pytest.fixture
def zeilen(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    monkeypatch.setattr(w, "PREFIX", "")
    out: list[str] = []
    monkeypatch.setattr(w, "log", out.append)
    return out


def _alarme(zeilen):
    return [z for z in zeilen if "⚠ Stephan:" in z]


def _gh(monkeypatch, issues, prs=(), fehler=False):
    aufrufe: list[tuple] = []

    def fake(*args):
        aufrufe.append(args)
        if fehler:
            raise RuntimeError("gh nicht erreichbar")
        if args[:2] == ("issue", "list"):
            return list(issues)
        if args[:2] == ("pr", "list"):
            return list(prs)
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    monkeypatch.setattr(w, "gh", fake)
    return aufrufe


def test_schwelle_ist_vierzig_minuten():
    assert w.VERWAIST_S == 2400


def test_altes_issue_ohne_pr_und_ohne_lauf_ist_verwaist(zeilen):
    assert w.verwaiste_auftraege([issue(274, 90), issue(5, 100)], [], now=T0) == [5, 274]


def test_grenze_liegt_bei_vierzig_minuten(zeilen):
    assert w.verwaiste_auftraege([issue(1, 39)], [], now=T0) == []
    assert w.verwaiste_auftraege([issue(1, 40)], [], now=T0) == [1]


def test_offener_pr_auf_dem_issue_branch_verhindert_die_meldung(zeilen):
    prs = [{"number": 9, "headRefName": w.issue_branch(274)}]
    assert w.verwaiste_auftraege([issue(274, 90), issue(275, 90)], prs, now=T0) == [275]


def test_lebende_belegung_verhindert_die_meldung_tote_nicht(zeilen):
    Path(w.CLAIM_DIR).mkdir(parents=True)
    Path(w.claim_path("issue274")).write_text(str(__import__("os").getpid()), encoding="utf-8")
    Path(w.claim_path("issue275")).write_text("0", encoding="utf-8")   # PID 0 lebt nie
    assert w.verwaiste_auftraege([issue(274, 90), issue(275, 90)], [], now=T0) == [275]


def test_unlesbares_datum_zaehlt_nicht_und_wirft_nicht(zeilen):
    kaputt = [{"number": 1, "updatedAt": "gestern"}, {"number": 2}]
    assert w.verwaiste_auftraege(kaputt, [], now=T0) == []


def test_melden_schreibt_je_issue_genau_eine_zeile_und_merkt_es(monkeypatch, zeilen):
    _gh(monkeypatch, [issue(274, 90)])
    state: dict = {}
    assert w.verwaiste_melden(state, now=T0) == [274]
    assert w.verwaiste_melden(state, now=T0 + 10 * MIN) == []
    alarme = _alarme(zeilen)
    assert len(alarme) == 1 and "#274" in alarme[0] and "seit 90 Minuten" in alarme[0]
    assert "\n" not in alarme[0]
    assert state[w.VERWAIST + "274"] is True
    assert json.loads(Path(w.STATE).read_text(encoding="utf-8")) == state


def test_melden_entfernt_den_schluessel_und_meldet_ein_neues_verwaisen_erneut(monkeypatch, zeilen):
    aufrufe = _gh(monkeypatch, [issue(274, 90)])
    state: dict = {}
    w.verwaiste_melden(state, now=T0)
    _gh(monkeypatch, [])                       # Label wurde zurückgesetzt oder Issue erledigt
    assert w.verwaiste_melden(state, now=T0 + 5 * MIN) == []
    assert state == {}
    _gh(monkeypatch, [issue(274, 120)])
    assert w.verwaiste_melden(state, now=T0 + 30 * MIN) == [274]
    assert len(_alarme(zeilen)) == 2
    assert aufrufe                              # die erste Abfrage lief tatsächlich


def test_melden_ohne_verwaiste_speichert_nichts(monkeypatch, zeilen):
    _gh(monkeypatch, [issue(1, 5)])
    state: dict = {}
    assert w.verwaiste_melden(state, now=T0) == []
    assert zeilen == [] and state == {}
    assert not Path(w.STATE).exists()


def test_melden_bei_gh_fehler_wirft_nicht_und_aendert_nichts(monkeypatch, zeilen):
    _gh(monkeypatch, [], fehler=True)
    state = {w.VERWAIST + "7": True}
    assert w.verwaiste_melden(state, now=T0) == []
    assert state == {w.VERWAIST + "7": True}
    assert _alarme(zeilen) == []
    assert any("Warnung: Prüfung auf verwaiste Aufträge fehlgeschlagen (RuntimeError)" in z for z in zeilen)


def test_nur_der_erste_slot_meldet(monkeypatch, zeilen):
    aufrufe = _gh(monkeypatch, [issue(274, 90)])
    monkeypatch.setattr(w, "WORKER_SLOT", "2")
    state: dict = {}
    assert w.verwaiste_melden(state, now=T0) == []
    assert aufrufe == [] and zeilen == [] and state == {}
    monkeypatch.setattr(w, "WORKER_SLOT", "")
    assert w.verwaiste_melden(state, now=T0) == [274]
