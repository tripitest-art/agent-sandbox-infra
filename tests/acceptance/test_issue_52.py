"""Akzeptanztests für Issue #<N>: Review-Skill für dieses Projekt.

Der Review-Takt lädt den Skill aus dem Projekt-Repo und speist den Abschnitt `## Schritt 5`
bis `## Schritt 7` in den Auftrag an den Reviewer (review_tick.py:549-551). Ohne Skill prüft
der Reviewer hier nur gegen den `### Reviewer`-Abschnitt der AGENTS.md.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "agent-sandbox-review" / "SKILL.md"


def test_skill_liegt_im_repo_mit_den_erwarteten_abschnitten():
    text = SKILL.read_text(encoding="utf-8")
    for abschnitt in ("## Schritt 5", "## Schritt 6", "## Schritt 7"):
        assert abschnitt in text


def test_skill_ist_im_projekt_eingetragen():
    daten = json.loads((ROOT / "sandbox" / "projects.example.json").read_text(encoding="utf-8"))
    eintrag = [p for p in daten["projects"] if p["name"] == "agent-sandbox-infra"][0]
    assert eintrag.get("review_skill") == "skills/agent-sandbox-review/SKILL.md"


def test_skill_nennt_die_infrastruktur_pruefpunkte():
    text = SKILL.read_text(encoding="utf-8")
    # Punkte, die es nur in diesem Projekt gibt: Regel 7 (Warnzeile im Log), Regel 8 (Engpass-
    # dateien), die Kopie nach /root durch install.sh und die Serverwirkung eines Fixes.
    for stichwort in ("⚠", "Regel 8", "install.sh", "/root"):
        assert stichwort in text
