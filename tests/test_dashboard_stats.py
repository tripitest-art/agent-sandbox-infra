"""Tests für sandbox/dashboard_stats.py (Review-/Merge-Statistik aus dem Log), ohne Netzwerk."""

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
_spec = importlib.util.spec_from_file_location("dashboard_stats", _SANDBOX / "dashboard_stats.py")
st = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_stats"] = st
_spec.loader.exec_module(st)

NOW = datetime(2026, 10, 16, 12, 0, 0)


def _ts(delta: timedelta) -> str:
    return (NOW - delta).strftime("%Y-%m-%d %H:%M:%S")


def _review(delta, msg, tag="review"):
    return f"{_ts(delta)} [{tag}] {msg}"


H = timedelta(hours=1)
FREI = "PR #7: Freigegeben, 0 Muss / 2 Kann, als Kommentar-Review gepostet (/root/reviews/pr7-abc.md)."
AEND = "PR #8: Änderungen nötig, 2 Muss / 1 Kann, als Kommentar-Review gepostet (/root/reviews/pr8-abc.md)."
MERGE = "PR #7: erfolgreich gemergt (--squash)."


@pytest.mark.parametrize("line,kind,project", [
    (_review(H, FREI), "freigegeben", "-"),
    (_review(H, AEND, "review:sc-digger"), "aenderungen", "sc-digger"),
    (_review(H, MERGE), "gemergt", "-"),
    (_review(H, "PR #9: Merge fehlgeschlagen (Branch geschützt)."), "merge_fehler", "-"),
    (_review(H, "PR #9: ⚠ Stephan: Freigegeben, Merge-Modus manuell: bitte prüfen und mergen."), "an_stephan", "-"),
    (f"{_ts(H)} [w2] ⚠ Stephan: issue61: TimeoutError: kein Modell", "worker_fehler", "-"),
    (f"{_ts(H)} ⚠ Stephan: -: Fehler: ohne Slot", "worker_fehler", "-"),
    ("\x1b[31m" + _review(H, MERGE) + "\x1b[0m\r", "gemergt", "-"),
])
def test_classify_erkennt_die_zeilen(line, kind, project):
    ts, k, p, _ = st.classify(line)
    assert (k, p) == (kind, project) and ts == NOW - H


@pytest.mark.parametrize("line", [
    "",
    "irgendeine Agentenzeile",
    _review(H, "PR #7: Entwurf Freigegeben, 0 Muss / 2 Kann (/root/reviews/x.md); REVIEW_POST=0, nicht gepostet."),
    _review(H, "PR #7: Freigegeben, aber REVIEW_AUTO_MERGE=0; kein Merge."),
    _review(H, "PR #7: Checks laufen noch, warte."),
    _review(H, "Issue #3: Wartet auf erledigt -> Label 'bereit' gesetzt."),
    f"{_ts(H)} [w1] issue5: starte Qwen Code mit coder.",
    "$ git commit -m '⚠ Stephan: nur Text'",
])
def test_classify_ignoriert_andere_zeilen(line):
    assert st.classify(line) is None


def test_kaputter_zeitstempel_wird_ignoriert():
    stats = st.ReviewStats()
    stats.feed("2026-13-45 99:99:99 [review] PR #7: erfolgreich gemergt (--squash).")
    assert stats.snapshot(NOW)["7d"]["gemergt"] == 0


def test_snapshot_zaehlt_fenster_quote_und_projekte():
    stats = st.ReviewStats()
    for line in (
        _review(2 * H, FREI), _review(3 * H, AEND), _review(4 * H, FREI), _review(5 * H, MERGE),
        _review(30 * H, FREI, "review:sc-digger"), _review(30 * H, MERGE, "review:sc-digger"),   # nur 7 Tage
        _review(8 * 24 * H, FREI),                                                              # zu alt
        f"{_ts(H)} [w1] ⚠ Stephan: issue1: Fehler",
        _review(H, "PR #9: ⚠ Stephan: bitte prüfen"),
        _review(H, "PR #9: Merge fehlgeschlagen (x)."),
    ):
        stats.feed(line)
    snap = stats.snapshot(NOW)
    assert snap["24h"] == {"freigegeben": 2, "aenderungen": 1, "gemergt": 1, "merge_fehler": 1,
                           "an_stephan": 1, "worker_fehler": 1, "quote": 67}
    assert snap["7d"]["freigegeben"] == 3 and snap["7d"]["gemergt"] == 2 and snap["7d"]["quote"] == 75
    assert list(snap["projekte"]) == ["sc-digger"]
    assert snap["projekte"]["sc-digger"]["freigegeben"] == 1 and snap["projekte"]["sc-digger"]["quote"] == 100


def test_quote_ist_none_ohne_reviews():
    snap = st.ReviewStats().snapshot(NOW)
    assert snap["24h"]["quote"] is None and snap["projekte"] == {}


def test_preload_liest_bis_zur_grenze_und_ueberspringt_angeschnittene_zeile(tmp_path):
    log = tmp_path / "worker.log"
    lines = [_review(H, FREI), _review(H, MERGE), _review(H, AEND)]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    log.write_bytes(data + b"nach der Grenze\n" + (_review(H, MERGE) + "\n").encode())
    stats = st.ReviewStats()
    stats.preload(str(log), upto=len(data))            # die letzte MERGE-Zeile liegt dahinter
    assert stats.snapshot(NOW)["24h"]["gemergt"] == 1 and stats.snapshot(NOW)["24h"]["aenderungen"] == 1

    klein = st.ReviewStats()
    klein.preload(str(log), upto=len(data), max_bytes=len(lines[2]) + 5)   # nur das Ende, erste Zeile angeschnitten
    assert klein.snapshot(NOW)["24h"] == {**klein.snapshot(NOW)["24h"], "freigegeben": 0, "gemergt": 0, "aenderungen": 1}


def test_preload_ohne_datei_ist_leer(tmp_path):
    stats = st.ReviewStats()
    stats.preload(str(tmp_path / "gibts-nicht.log"), upto=100)
    assert len(stats.events) == 0


def test_ereignisse_sind_begrenzt():
    stats = st.ReviewStats()
    for _ in range(st.MAX_EVENTS + 50):
        stats.feed(_review(H, MERGE))
    assert len(stats.events) == st.MAX_EVENTS


# --- Reviewer je Modell --------------------------------------------------------------------------

def _frei(model=None):
    suffix = f", Reviewer {model}" if model else ""
    return f"PR #7: Freigegeben, 0 Muss / 2 Kann, als Kommentar-Review gepostet{suffix} (/root/reviews/pr7-abc.md)."


def _aend(model=None):
    suffix = f", Reviewer {model}" if model else ""
    return f"PR #8: Änderungen nötig, 2 Muss / 1 Kann, als Kommentar-Review gepostet{suffix} (/root/reviews/pr8-abc.md)."


@pytest.mark.parametrize("msg,model", [
    (_frei("coder-glm"), "coder-glm"),
    (_aend("glm-4.6-longctx"), "glm-4.6-longctx"),
    (_aend("ohne-Modell"), "ohne-Modell"),
    (_frei(), "unbekannt"),            # Zeile aus der Zeit vor der Änderung in review_tick.py
])
def test_classify_liest_den_reviewer_aus_der_zeile(msg, model):
    assert st.classify(_review(H, msg))[3] == model


def test_classify_setzt_bei_anderen_arten_keinen_reviewer():
    for line in (_review(H, MERGE), _review(H, "PR #9: ⚠ Stephan: bitte prüfen"),
                 f"{_ts(H)} [w1] ⚠ Stephan: issue1: Fehler"):
        assert st.classify(line)[3] == "-"


def test_snapshot_zaehlt_reviews_je_modell_ueber_sieben_tage():
    stats = st.ReviewStats()
    for line in (
        _review(2 * H, _frei("coder-glm")), _review(3 * H, _frei("coder-glm")), _review(4 * H, _aend("coder-glm")),
        _review(30 * H, _aend("ohne-Modell")), _review(30 * H, _frei()),
        _review(8 * 24 * H, _frei("alt-modell")),                      # zu alt
        _review(H, MERGE),                                              # kein Review: zählt nicht je Modell
    ):
        stats.feed(line)
    modelle = stats.snapshot(NOW)["modelle"]
    assert list(modelle) == ["coder-glm", "ohne-Modell", "unbekannt"]
    assert (modelle["coder-glm"]["freigegeben"], modelle["coder-glm"]["aenderungen"], modelle["coder-glm"]["quote"]) == (2, 1, 67)
    assert modelle["ohne-Modell"]["quote"] == 0 and modelle["unbekannt"]["quote"] == 100
    assert modelle["coder-glm"]["gemergt"] == 0


def test_snapshot_ohne_reviews_hat_keine_modelle():
    assert st.ReviewStats().snapshot(NOW)["modelle"] == {}


# --- Anbindung an dashboard.py ------------------------------------------------------------------

def test_logbuffer_zaehlt_nachgelesene_zeilen_ohne_doppelte(tmp_path):
    spec = importlib.util.spec_from_file_location("dashboard", _SANDBOX / "dashboard.py")
    d = importlib.util.module_from_spec(spec)
    sys.modules["dashboard"] = d
    spec.loader.exec_module(d)
    log = tmp_path / "worker.log"
    log.write_bytes((_review(H, MERGE) + "\n").encode())
    stats = st.ReviewStats()
    buf = d.LogBuffer(str(log), stats=stats)
    assert stats.snapshot(NOW)["24h"]["gemergt"] == 1               # vorgeladen
    with open(log, "ab") as fh:
        fh.write((_review(H, MERGE) + "\n" + _review(H, FREI) + "\n").encode())
    buf.poll()
    buf.poll()
    snap = stats.snapshot(NOW)["24h"]
    assert snap["gemergt"] == 2 and snap["freigegeben"] == 1        # nichts doppelt

    dash = d.Dashboard(d.StatusCache(lambda: {"projekte": [], "budgets": {}}), buf,
                       slot_dir=str(tmp_path / "slots"), clock=lambda: NOW)
    assert dash.state()["statistik"]["24h"]["gemergt"] == 2
    ohne = d.Dashboard(d.StatusCache(lambda: {"projekte": [], "budgets": {}}), d.LogBuffer(str(log)),
                       slot_dir=str(tmp_path / "slots"), clock=lambda: NOW)
    assert ohne.state()["statistik"] is None
