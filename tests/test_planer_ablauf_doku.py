"""Struktur der Planer-Doku: Abschnitt „Korrektur nach der Übernahme“ (Issue #341)."""

from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "planer-ablauf.md"
HEADING = "## Korrektur nach der Übernahme"


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def _abschnitt() -> str:
    text = _text()
    assert HEADING in text, "Abschnitt fehlt"
    return text.split(HEADING, 1)[1].split("\n## ", 1)[0]


def test_abschnitt_steht_direkt_vor_den_agenten_labels():
    text = _text()
    assert HEADING in text
    naechste_ueberschrift = text.split(HEADING, 1)[1].split("\n## ", 1)[1]
    assert naechste_ueberschrift.startswith("Agenten-Labels")


def test_abschnitt_hat_einen_punkt_je_zustaendigem():
    punkte = [zeile for zeile in _abschnitt().splitlines() if zeile.startswith("- **")]
    assert len(punkte) == 4, f"erwartet: wann, Planer, Reviewer, Label am PR; gefunden: {punkte}"
    for name in ("Planer", "Reviewer", "Stephan"):
        assert any(name in punkt for punkt in punkte), f"kein Punkt für {name}"


def test_wann_punkt_verlangt_beleg_und_sonst_kommentar():
    abschnitt = _abschnitt()
    assert "nachweislich falsch oder unerfüllbar" in abschnitt
    assert "Sonst genügt ein Kommentar" in abschnitt


def test_planer_setzt_das_label_am_issue_und_kommentiert():
    abschnitt = _abschnitt()
    assert "`freigabe-geschützt` am **Issue**" in abschnitt
    assert "Überschrift `Korrektur nach Übernahme`" in abschnitt


def test_reviewer_prueft_guard_zeile_vor_dem_verdikt():
    abschnitt = _abschnitt()
    assert "Guard-Zeile" in abschnitt
    assert "Freigegeben" in abschnitt


def test_label_am_pr_setzt_stephan_nach_dem_review():
    abschnitt = _abschnitt()
    assert 'gh pr edit <PR> --add-label "freigabe-geschützt"' in abschnitt
    assert "Der Worker setzt es nie" in abschnitt


def test_abschnitt_nennt_das_beispiel_ohne_zugangsdaten():
    abschnitt = _abschnitt()
    assert "#317/#331" in abschnitt
    for verboten in ("http://", "https://", "@", "192.168.", "10.0."):
        assert verboten not in abschnitt, f"{verboten!r} gehört nicht in den Abschnitt"


def test_planer_ablauf_verweist_auf_den_neuen_abschnitt():
    text = _text()
    verweis = text.split("If an acceptance test must be corrected", 1)[1].split("\n## ", 1)[0]
    assert "Korrektur nach der Übernahme" in verweis
