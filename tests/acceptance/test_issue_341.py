from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "planer-ablauf.md"
HEADING = "## Korrektur nach der Übernahme"


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def _abschnitt() -> str:
    text = _text()
    assert HEADING in text, "Abschnitt fehlt"
    rest = text.split(HEADING, 1)[1]
    return rest.split("\n## ", 1)[0]


def test_abschnitt_steht_vor_den_agenten_labels():
    text = _text()
    assert HEADING in text
    assert text.index(HEADING) < text.index("## Agenten-Labels")


def test_abschnitt_nennt_label_kommentar_und_beteiligte():
    abschnitt = _abschnitt()
    for muss in ("freigabe-geschützt", "Korrektur nach Übernahme", "Reviewer", "Stephan", "in-arbeit"):
        assert muss in abschnitt, f"{muss!r} fehlt im Abschnitt"


def test_abschnitt_sagt_dass_der_worker_das_label_nicht_setzt():
    abschnitt = _abschnitt()
    assert "Worker setzt" in abschnitt and "nie" in abschnitt


def test_planer_ablauf_verweist_auf_den_abschnitt():
    text = _text()
    vor = text.split("## Agenten-Labels", 1)[0]
    assert "If an acceptance test must be corrected" in vor
    assert "Korrektur nach der Übernahme" in vor.split("If an acceptance test must be corrected", 1)[1]
