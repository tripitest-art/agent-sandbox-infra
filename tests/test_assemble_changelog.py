"""Unit-Tests für sandbox/assemble_changelog.py (#204)."""
from pathlib import Path
import pytest
from sandbox.assemble_changelog import assemble_fragments


def test_assemble_ohne_verzeichnis_oder_changelog(tmp_path):
    assert assemble_fragments(tmp_path / "CHANGELOG.md", tmp_path / "nicht-da") == 0

    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    assert assemble_fragments(tmp_path / "nicht-da.md", frag_dir) == 0


def test_assemble_leeres_verzeichnis(tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text("# Changelog\n\n## Unreleased\n\n### Geändert\n", encoding="utf-8")
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / ".gitkeep").touch()

    assert assemble_fragments(cl, frag_dir) == 0
    assert (frag_dir / ".gitkeep").exists()


def test_assemble_sortiert_und_mehrzeilig(tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text("# Changelog\n\n## Unreleased\n\n### Geändert\n- vorher (#0)\n", encoding="utf-8")
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / "002.md").write_text("- zeile 2 (#2)\n- zeile 2b (#2)\n", encoding="utf-8")
    (frag_dir / "001.md").write_text("- zeile 1 (#1)\n", encoding="utf-8")

    count = assemble_fragments(cl, frag_dir)
    assert count == 2
    assert not (frag_dir / "001.md").exists()
    assert not (frag_dir / "002.md").exists()

    content = cl.read_text(encoding="utf-8")
    z1 = content.find("- zeile 1 (#1)")
    z2 = content.find("- zeile 2 (#2)")
    z0 = content.find("- vorher (#0)")
    assert z1 != -1 and z2 != -1 and z0 != -1
    assert z1 < z2 < z0


def test_assemble_ohne_geaendert_ueberschrift(tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text("# Changelog\n\n## Unreleased\n", encoding="utf-8")
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / "100.md").write_text("- ding (#100)\n", encoding="utf-8")

    count = assemble_fragments(cl, frag_dir)
    assert count == 1
    content = cl.read_text(encoding="utf-8")
    assert "### Geändert" in content
    assert "- ding (#100)" in content


def test_assemble_leere_fragmente_werden_aufgeraeumt(tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text("# Changelog\n\n## Unreleased\n\n### Geändert\n- a\n", encoding="utf-8")
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / "leer.md").write_text("   \n\n", encoding="utf-8")

    count = assemble_fragments(cl, frag_dir)
    assert count == 1
    assert not (frag_dir / "leer.md").exists()
    assert cl.read_text(encoding="utf-8") == "# Changelog\n\n## Unreleased\n\n### Geändert\n- a\n"
