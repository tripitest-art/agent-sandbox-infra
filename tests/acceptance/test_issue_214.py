from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_roadmap_node_g_not_red():
    content = (REPO_ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    assert "style G fill:#f8d7da" not in content
    assert "Aktuell noch manueller Zwischenschritt" not in content


def test_backup_md_no_ha_references():
    content = (REPO_ROOT / "docs" / "backup.md").read_text(encoding="utf-8")
    assert "ha-status.timer" not in content
    assert "Home Assistant" not in content
    assert "<von Stephan zu ergänzen>" not in content


def test_mehrere_projekte_json_consistent():
    content = (REPO_ROOT / "docs" / "mehrere-projekte.md").read_text(encoding="utf-8")
    assert '"auto_merge": true' in content
    assert '"sandbox/*"' not in content
