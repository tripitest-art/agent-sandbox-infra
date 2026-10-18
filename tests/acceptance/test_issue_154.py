from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_readme_enthaelt_keine_ha_sektion_oder_diagramm_knoten():
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert "### 4. Home Assistant anbinden" not in readme
    assert "ha-status" not in readme
    assert "qwen-webhook" not in readme
    assert "/etc/ha-status.env" not in readme
    assert "/etc/qwen-webhook.env" not in readme


def test_autodeploy_und_skill_enthalten_keinen_ha_verweis():
    autodeploy = (_ROOT / "docs" / "autodeploy.md").read_text(encoding="utf-8")
    skill = (_ROOT / "skills" / "agent-sandbox-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "Home Assistant" not in autodeploy
    assert "Home Assistant" not in skill
