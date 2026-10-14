"""Akzeptanztests für Issue #<N>: Die Vertragsdokumente liegen im Repo, AGENTS.md verweist nicht ins Leere.

Vorher stand in AGENTS.md „Ablauf: wie in sc-digger (`ENTWICKLUNG.md` dort, „Planer-Ablauf")" —
für einen Agenten in diesem Repo ein toter Verweis: die Datei gibt es hier nicht, und Regel 9
verbietet ihm, in sc-digger zu lesen. Dasselbe gilt für `MCP.md`.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def test_planer_ablauf_liegt_im_repo():
    text = (ROOT / "docs" / "planer-ablauf.md").read_text(encoding="utf-8")
    for abschnitt in ("## Planer-Ablauf", "## Agenten-Labels"):
        assert abschnitt in text
    # Die Beispiele müssen aus diesem Repo stammen, nicht aus sc-digger kopiert sein.
    assert "FakeGH" in text
    assert "sc_digger" not in text


def test_mcp_anleitung_liegt_im_repo():
    text = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    for abschnitt in ("## Befehle", "## Regeln"):
        assert abschnitt in text


def test_agents_md_verweist_nicht_auf_dokumente_anderer_repos():
    assert "ENTWICKLUNG.md" not in AGENTS
    assert "MCP.md in sc-digger" not in AGENTS
    assert "docs/planer-ablauf.md" in AGENTS
    assert "docs/mcp.md" in AGENTS


def test_agents_md_bleibt_unter_der_grenze():
    # Antigravity schneidet Regeldateien bei 12 000 Zeichen ab (tests/test_agents_md.py).
    assert len(AGENTS) < 12_000
