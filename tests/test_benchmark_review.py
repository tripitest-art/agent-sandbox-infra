"""Grenzfälle von `parse_urteil()`/`score_review()` und Aufbau des Review-Prompts (#336)."""

import re

import pytest

from sandbox.core import benchmark_review as br


def _aufgabe(id_):
    return next(t for t in br.REVIEW_TASKS if t["id"] == id_)


def test_parse_urteil_lehnt_nicht_text_ab():
    assert br.parse_urteil(None) is None
    assert br.parse_urteil(42) is None
    assert br.parse_urteil(["Freigegeben"]) is None


def test_parse_urteil_nimmt_die_erste_nicht_leere_zeile():
    assert br.parse_urteil("\n \t\n**Freigegeben**\nGrund") == br.URTEIL_FREIGEGEBEN
    assert br.parse_urteil("Grund\nFreigegeben") is None


def test_parse_urteil_verlangt_den_wortanfang():
    assert br.parse_urteil("Ich finde: Freigegeben") is None
    assert br.parse_urteil("Freigegeben2") is None
    assert br.parse_urteil("Änderungen nötig!") == br.URTEIL_AENDERUNGEN


def test_parse_urteil_akzeptiert_ersatzschreibung():
    for antwort in ("AENDERUNGEN NOETIG", "aenderungen noetig.", "**Aenderungen noetig:**"):
        assert br.parse_urteil(antwort) == br.URTEIL_AENDERUNGEN


def test_parse_urteil_entfernt_anfuehrungszeichen():
    for antwort in ("«Freigegeben»", "„Freigegeben“", "'Freigegeben'"):
        assert br.parse_urteil(antwort) == br.URTEIL_FREIGEGEBEN


def test_parse_urteil_lehnt_beide_urteile_in_einer_zeile_ab():
    assert br.parse_urteil("Freigegeben oder Änderungen nötig") is None
    assert br.parse_urteil("Änderungen nötig, nicht freigegeben") is None


def test_parse_urteil_lehnt_leerraum_und_schmuck_ohne_wort_ab():
    assert br.parse_urteil("   \n \t ") is None
    assert br.parse_urteil("**") is None
    assert br.parse_urteil("...") is None


def test_score_review_meldet_kein_urteil_vor_allem_anderen():
    t = _aufgabe("off_by_one")
    assert br.score_review(t, "") == {"bestanden": False, "grund": "kein Urteil"}
    assert br.score_review(t, None) == {"bestanden": False, "grund": "kein Urteil"}
    assert br.score_review(t, "Kein Urteil hier") == {"bestanden": False, "grund": "kein Urteil"}


def test_score_review_prueft_das_urteil_vor_den_merkmalen():
    t = _aufgabe("off_by_one")
    antwort = "Freigegeben\ndas letzte Element fehlt, len(werte) - 1 ist falsch"
    assert br.score_review(t, antwort) == {"bestanden": False, "grund": "falsches Urteil"}


def test_score_review_zaehlt_die_urteilszeile_nicht_mit():
    t = _aufgabe("off_by_one")
    antwort = "Änderungen nötig: letztes Element fehlt, len(werte) - 1\nSonst unauffällig."
    assert br.score_review(t, antwort) == {"bestanden": False, "grund": "Fehler nicht erkannt"}


def test_score_review_besteht_mit_merkmalen_in_der_begruendung():
    t = _aufgabe("stiller_fehler")
    antwort = "Änderungen nötig\n`except Exception: pass` verschluckt den Fehler (Regel 7)."
    assert br.score_review(t, antwort)["bestanden"] is True


def test_score_review_ueberspringt_kaputte_regexe():
    task = {
        "erwartet": br.URTEIL_AENDERUNGEN,
        "merkmale": [["(", r"letzte element"], ["grenze"]],
    }
    assert br.score_review(task, "Änderungen nötig\ndan der grenze")["bestanden"] is False
    antwort = "Änderungen nötig\ndas letzte element an der grenze"
    assert br.score_review(task, antwort)["bestanden"] is True


def test_score_review_vertraegt_nicht_text_eintraege():
    task = {"erwartet": br.URTEIL_AENDERUNGEN, "merkmale": [[None, "grenze"]]}
    assert br.score_review(task, "Änderungen nötig\nnur text")["bestanden"] is False
    assert br.score_review(task, "Änderungen nötig\ngrenze falsch")["bestanden"] is True


def test_score_review_leere_gruppe_gilt_als_nicht_erfuellt():
    task = {"erwartet": br.URTEIL_AENDERUNGEN, "merkmale": [["grenze"], []]}
    assert br.score_review(task, "Änderungen nötig\ngrenze falsch") == {
        "bestanden": False,
        "grund": "Fehler nicht erkannt",
    }


def test_score_review_liefert_genau_bestanden_und_grund():
    assert set(br.score_review(_aufgabe("sauber"), "Freigegeben")) == {"bestanden", "grund"}


def test_build_review_prompt_enthaelt_diff_und_format():
    t = _aufgabe("off_by_one")
    prompt = br.build_review_prompt(t)
    assert prompt.count(t["diff"]) == 1
    assert br.URTEIL_FREIGEGEBEN in prompt
    assert br.URTEIL_AENDERUNGEN in prompt
    assert "Urteil" in prompt


def test_build_review_prompt_verraet_die_loesung_nicht():
    for t in br.REVIEW_TASKS:
        prompt = br.build_review_prompt(t).replace(t["diff"], "")
        assert t["titel"] not in prompt, t["id"]
        rumpf = "\n".join(t["referenz_antwort"].splitlines()[1:]).strip()
        assert not rumpf or rumpf not in prompt, t["id"]


def test_build_review_prompt_ist_nur_im_diff_verschieden():
    ohne_diff = set()
    for t in br.REVIEW_TASKS:
        ohne_diff.add(br.build_review_prompt(t).replace(t["diff"], ""))
    assert len(ohne_diff) == 1


def test_jede_merkmalsgruppe_trifft_die_referenzantwort():
    for t in br.REVIEW_TASKS:
        rumpf = "\n".join(t["referenz_antwort"].splitlines()[1:])
        for gruppe in t["merkmale"]:
            assert any(re.search(rx, rumpf, re.IGNORECASE) for rx in gruppe), (t["id"], gruppe)


@pytest.mark.parametrize("t", br.REVIEW_TASKS, ids=lambda t: t["id"])
def test_referenzantwort_ist_einzeilig_nicht_bestanden(t):
    if t["erwartet"] != br.URTEIL_AENDERUNGEN:
        assert br.score_review(t, " ".join(t["referenz_antwort"].split()))["bestanden"] is True
        return
    assert br.score_review(t, " ".join(t["referenz_antwort"].split())) == {
        "bestanden": False,
        "grund": "Fehler nicht erkannt",
    }
