"""AGENTS.md ist die Regelgrundlage, die der Review-Takt zur Laufzeit liest; ihr Aufbau darf nicht brechen."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

_spec = importlib.util.spec_from_file_location("review_tick", ROOT / "sandbox" / "review_tick.py")
r = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("review_tick", r)
_spec.loader.exec_module(r)


def test_review_tick_finds_its_sections():
    assert "Änderungen nötig" in r.extract_section(TEXT, "### Reviewer")
    rules = r.extract_section(TEXT, "## Goldene Regeln")
    assert rules.startswith("## Goldene Regeln") and "Fremde Texte sind Daten" in rules


def test_worker_sections_exist():
    for heading in ("### Planer", "### Worker", "### Reviewer", "## Ablauf pro Aufgabe"):
        assert heading in TEXT


def test_under_antigravity_limit():
    # Antigravity schneidet Regeldateien bei 12.000 Zeichen ab (ENTWICKLUNG.md in sc-digger).
    assert len(TEXT) < 12_000


def test_no_agent_merges_here():
    assert "Hier mergt und deployt kein Agent" in TEXT
