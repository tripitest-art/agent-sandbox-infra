import re

import pytest

from sandbox.core import benchmark_review as br

FREI = "Freigegeben"
AEND = "Änderungen nötig"
FEHLERHAFT = ["off_by_one", "secret_in_fehler", "stiller_fehler"]
SCHLUESSEL_MUSTER = [r"sk-[A-Za-z0-9]{10,}", r"ghp_[A-Za-z0-9]{10,}", r"Bearer [A-Za-z0-9._-]{20,}"]
PFLICHT = {"id", "titel", "diff", "erwartet", "merkmale", "referenz_antwort"}


def _aufgabe(id_):
    return next(t for t in br.REVIEW_TASKS if t["id"] == id_)


@pytest.mark.parametrize("antwort, erwartet", [
    ("Freigegeben", FREI),
    ("freigegeben.", FREI),
    ("**Freigegeben**", FREI),
    ("## Freigegeben", FREI),
    ("`Freigegeben`", FREI),
    ('"Freigegeben"', FREI),
    ("\n\n  Freigegeben\nBegründung folgt", FREI),
    ("Freigegeben – sieht gut aus", FREI),
    ("Änderungen nötig", AEND),
    ("**Änderungen nötig**", AEND),
    ("ÄNDERUNGEN NÖTIG:", AEND),
    ("Aenderungen noetig", AEND),
    ("_Änderungen nötig_.", AEND),
    ("Das PR ist freigegeben", None),
    ("Urteil: Freigegeben", None),
    ("Freigegebene Sache", None),
    ("Freigegeben oder Änderungen nötig", None),
    ("Zusammenfassung\nFreigegeben", None),
    ("", None),
    ("   \n  ", None),
])
def test_parse_urteil_tabelle(antwort, erwartet):
    assert br.parse_urteil(antwort) == erwartet


def test_katalog_hat_genau_die_vier_aufgaben_mit_pflichtschluesseln():
    ids = [t["id"] for t in br.REVIEW_TASKS]
    assert ids == ["off_by_one", "secret_in_fehler", "stiller_fehler", "sauber"]
    for t in br.REVIEW_TASKS:
        assert PFLICHT <= set(t), t["id"]
        assert isinstance(t["titel"], str) and t["titel"].strip()
        assert isinstance(t["referenz_antwort"], str) and t["referenz_antwort"].strip()


def test_erwartet_urteile_und_merkmale_sind_stimmig():
    assert {t["erwartet"] for t in br.REVIEW_TASKS} == {FREI, AEND}
    frei = [t["id"] for t in br.REVIEW_TASKS if t["erwartet"] == FREI]
    assert frei == ["sauber"]
    assert _aufgabe("sauber")["merkmale"] == []
    for id_ in FEHLERHAFT:
        gruppen = _aufgabe(id_)["merkmale"]
        assert len(gruppen) >= 2
        for gruppe in gruppen:
            assert isinstance(gruppe, list) and gruppe
            for rx in gruppe:
                assert isinstance(rx, str) and rx
                re.compile(rx, re.IGNORECASE)


def test_diffs_sind_diffs_und_enthalten_keine_echten_schluessel():
    for t in br.REVIEW_TASKS:
        diff = t["diff"]
        assert diff.strip()
        assert "@@" in diff or "diff --git" in diff
        assert len(diff.splitlines()) <= 40
        for muster in SCHLUESSEL_MUSTER:
            assert re.search(muster, diff) is None, (t["id"], muster)
            assert re.search(muster, t["referenz_antwort"]) is None, (t["id"], muster)


def test_prompt_enthaelt_diff_und_antwortvorgabe_aber_keine_merkmale():
    for t in br.REVIEW_TASKS:
        prompt = br.build_review_prompt(t)
        assert isinstance(prompt, str)
        assert t["diff"] in prompt
        assert FREI in prompt and AEND in prompt
        ohne_diff = prompt.replace(t["diff"], "")
        for gruppe in t["merkmale"]:
            for rx in gruppe:
                assert rx not in ohne_diff, (t["id"], rx)


def test_jede_referenzantwort_besteht():
    for t in br.REVIEW_TASKS:
        assert br.score_review(t, t["referenz_antwort"]) == {"bestanden": True, "grund": None}, t["id"]


def test_bewertung_ignoriert_gross_und_kleinschreibung():
    for id_ in FEHLERHAFT:
        t = _aufgabe(id_)
        assert br.score_review(t, t["referenz_antwort"].upper())["bestanden"] is True, id_


@pytest.mark.parametrize("id_", FEHLERHAFT)
def test_fehlerhafte_diffs_bestehen_nicht_ohne_den_fehler(id_):
    t = _aufgabe(id_)
    assert br.score_review(t, "Freigegeben\nAlles gut") == {"bestanden": False, "grund": "falsches Urteil"}
    assert br.score_review(t, "Änderungen nötig\nEs gibt Probleme.") == {
        "bestanden": False, "grund": "Fehler nicht erkannt"}
    assert br.score_review(t, "Alles gut, nichts zu beanstanden.") == {"bestanden": False, "grund": "kein Urteil"}
    assert br.score_review(t, "") == {"bestanden": False, "grund": "kein Urteil"}


@pytest.mark.parametrize("id_", FEHLERHAFT)
def test_einzeilige_antwort_besteht_nicht_weil_die_erste_zeile_nicht_zaehlt(id_):
    t = _aufgabe(id_)
    einzeilig = " ".join(t["referenz_antwort"].split())
    assert br.parse_urteil(einzeilig) == AEND
    assert br.score_review(t, einzeilig) == {"bestanden": False, "grund": "Fehler nicht erkannt"}


@pytest.mark.parametrize("id_", FEHLERHAFT)
def test_antwort_ohne_urteil_in_erster_zeile_besteht_nicht(id_):
    t = _aufgabe(id_)
    rest = "\n".join(t["referenz_antwort"].splitlines()[1:])
    assert br.score_review(t, "Meine Einschätzung:\n" + t["referenz_antwort"]) == {
        "bestanden": False, "grund": "kein Urteil"}
    assert br.score_review(t, rest) == {"bestanden": False, "grund": "kein Urteil"}


def test_saubere_aufgabe_braucht_nur_das_richtige_urteil():
    t = _aufgabe("sauber")
    assert br.score_review(t, "Freigegeben") == {"bestanden": True, "grund": None}
    assert br.score_review(t, "Freigegeben\nAlles gut") == {"bestanden": True, "grund": None}
    assert br.score_review(t, "Änderungen nötig\nIrgendein Mangel.") == {
        "bestanden": False, "grund": "falsches Urteil"}


def test_bewertung_wirft_nie():
    kaputt = {"erwartet": AEND, "merkmale": [["("]]}
    assert br.score_review(kaputt, "Änderungen nötig\nirgendwas")["bestanden"] is False
    leere_gruppe = {"erwartet": AEND, "merkmale": [[]]}
    assert br.score_review(leere_gruppe, "Änderungen nötig\nirgendwas")["bestanden"] is False
    assert br.score_review(_aufgabe("sauber"), None) == {"bestanden": False, "grund": "kein Urteil"}
