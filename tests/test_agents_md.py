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


def test_no_agent_deploys_here():
    assert "Hier deployt kein Agent, Aufsichtsdateien merged nur Stephan" in TEXT
    assert "manual_paths" in TEXT


def test_vertragsdokumente_liegen_im_repo():
    """AGENTS.md verweist nicht ins Leere: die Dokumente liegen hier, nicht in sc-digger.

    Vorher stand dort „Ablauf: wie in sc-digger (`ENTWICKLUNG.md` dort …)" — für einen Agenten in
    diesem Repo ein toter Verweis, weil Regel 9 ihm das Lesen dort verbietet.
    """
    for fremd in ("ENTWICKLUNG.md", "MCP.md in sc-digger"):
        assert fremd not in TEXT
    for pfad in ("docs/planer-ablauf.md", "docs/mcp.md"):
        assert pfad in TEXT, f"AGENTS.md verweist nicht auf {pfad}"
        assert (ROOT / pfad).is_file(), f"{pfad} fehlt"
