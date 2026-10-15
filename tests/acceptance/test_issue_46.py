"""Akzeptanztests für Issue #<N>: Abhängigkeiten nur außerhalb von Code-Blöcken, sonst Meldung.

Die Beispielzeilen sind bewusst zusammengesetzt statt wörtlich zitiert: stünde eine Beispielzeile
als Zeichenkette im Issue-Text, läse der Takt dieses Issue selbst als abhängig von den
Beispielnummern — genau der Fehler, den die erste Regel hier behebt.
"""
import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "review_tick", Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py")
r = importlib.util.module_from_spec(_SPEC)
sys.modules["review_tick"] = r
_SPEC.loader.exec_module(r)


def test_zeile_ausserhalb_des_blocks_zaehlt():
    zeile = "Wartet auf: " + ", ".join(f"#{n}" for n in (29, 30))
    assert r.parse_dependencies(zeile) == [29, 30]


def test_zeilen_in_code_bloecken_zaehlen_nicht():
    probe = "```\n" + "Wartet auf: " + ", ".join(f"#{n}" for n in (14, 22)) + "\n```"
    assert r.parse_dependencies(probe) == []


def test_blockiertes_issue_ohne_zeile_wird_einmal_gemeldet(monkeypatch, capsys):
    """Ohne erkennbare Zeile wird gemeldet statt still übersprungen (Regel 7), aber nur einmal."""
    state: dict = {}
    monkeypatch.setattr(r, "gh_json", lambda *a, **k: [
        {"number": 8, "title": "T", "body": "### Kontext\n\nkein Hinweis auf eine Abhängigkeit"}])
    monkeypatch.setattr(r, "load_state", lambda: state)
    monkeypatch.setattr(r, "save_state", lambda s: None)
    monkeypatch.setattr(r, "POST", True)

    assert r.unblock_dependent_issues() == []
    out = capsys.readouterr().out
    assert "Issue #8: blockiert, aber keine Abhängigkeit erkannt" in out

    assert r.unblock_dependent_issues() == []
    assert capsys.readouterr().out == ""      # zweiter Takt: keine zweite Zeile
