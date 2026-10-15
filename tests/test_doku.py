"""Die Doku darf dem Code nicht davonlaufen: Dateiübersicht, Changelog und Roadmap-Stand."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_readme_nennt_jede_sandbox_datei():
    """Neue Skripte unter sandbox/ müssen in der Repository-Struktur der README auftauchen."""
    fehlend = [
        p.name
        for p in sorted((ROOT / "sandbox").iterdir())
        if p.is_file() and p.suffix in {".py", ".sh", ".json"} and p.name not in README
    ]
    assert not fehlend, f"README.md nennt diese Dateien nicht: {fehlend}"


def test_readme_nennt_jede_doku_datei():
    fehlend = [p.name for p in sorted((ROOT / "docs").glob("*.md")) if p.name not in README]
    assert not fehlend, f"README.md verlinkt diese Dokumente nicht: {fehlend}"


def test_readme_links_zeigen_auf_vorhandene_dateien():
    tot = []
    for ziel in re.findall(r"\]\(([^)#\s]+)\)", README):
        if ziel.startswith(("http://", "https://")):
            continue
        if not (ROOT / ziel).exists():
            tot.append(ziel)
    assert not tot, f"Tote Verweise in README.md: {tot}"


def test_changelog_hat_unreleased_und_pr_nummern():
    assert "## Unreleased" in CHANGELOG
    assert re.search(r"\(#\d+\)", CHANGELOG), "Einträge nennen keine PR-Nummer"


def test_roadmap_und_readme_stimmen_bei_stufe_2_ueberein():
    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    assert "In Arbeit" not in README and "In Arbeit" not in roadmap, "Stufe-Stand veraltet"
