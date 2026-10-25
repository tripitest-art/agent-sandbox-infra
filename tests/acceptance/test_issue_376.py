"""Akzeptanztest: Freigabe durch die App, Merge durch das Gate."""

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_freigabe", _PATH)
rt = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_freigabe"] = rt
_spec.loader.exec_module(rt)

FREI = "**Freigegeben**"
NEIN = "**Änderungen nötig**"


def test_freigabe_mit_app_wird_approve_und_bekommt_das_label():
    assert rt.review_art(FREI, True) == ("--approve", "review-freigabe")


def test_freigabe_ohne_app_bleibt_kommentar_ohne_label():
    assert rt.review_art(FREI, False) == ("--comment", "")


def test_aenderungen_bleiben_kommentar_auch_mit_app():
    assert rt.review_art(NEIN, True) == ("--comment", "")


def test_unbekanntes_urteil_postet_kein_review():
    assert rt.review_art("**Vielleicht**", True) is None


def test_mit_app_mergt_der_takt_nie():
    assert rt.darf_selbst_mergen(FREI, True, True) is False


def test_ohne_app_mergt_der_takt_wie_bisher():
    assert rt.darf_selbst_mergen(FREI, False, True) is True


def test_ohne_auto_merge_mergt_der_takt_nie():
    assert rt.darf_selbst_mergen(FREI, False, False) is False


def test_nur_eine_freigabe_erlaubt_den_merge_ueberhaupt():
    assert rt.darf_selbst_mergen(NEIN, False, True) is False
