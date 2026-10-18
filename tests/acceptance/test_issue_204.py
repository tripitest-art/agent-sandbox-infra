import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
def _ac():
    _PATH = _ROOT / "sandbox" / "assemble_changelog.py"
    _spec = importlib.util.spec_from_file_location("assemble_changelog", _PATH)
    ac = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(ac)
    return ac


def test_changelog_dir_existiert():
    assert (_ROOT / "changelog.d").is_dir()
    assert (_ROOT / "changelog.d" / ".gitkeep").exists()


def test_assemble_fuegt_fragmente_ein_und_loescht_sie(tmp_path):
    ac = _ac()
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text("# Changelog\n\n## Unreleased\n\n### Geändert\n- altes: ding (#1)\n", encoding="utf-8")
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / ".gitkeep").touch()
    (frag_dir / "200.md").write_text("- worker_tick: limits (#200)\n", encoding="utf-8")
    (frag_dir / "201.md").write_text("- install.sh: takt (#201)\n", encoding="utf-8")

    count = ac.assemble_fragments(cl, frag_dir)
    assert count == 2
    assert not (frag_dir / "200.md").exists()
    assert not (frag_dir / "201.md").exists()
    assert (frag_dir / ".gitkeep").exists()

    content = cl.read_text(encoding="utf-8")
    assert "- worker_tick: limits (#200)" in content
    assert "- install.sh: takt (#201)" in content
    assert "- altes: ding (#1)" in content


def test_agents_md_nennt_changelog_d():
    agents = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "changelog.d/" in agents
