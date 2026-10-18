"""Sammelt Fragmente aus changelog.d/ ein und fügt sie in CHANGELOG.md ein (#204)."""
from pathlib import Path


def assemble_fragments(changelog_path: Path, fragments_dir: Path) -> int:
    """Liest alle *.md-Dateien aus fragments_dir (sortiert nach Dateiname),
    fügt deren Zeilen unter '### Geändert' in changelog_path ein und löscht
    die Fragment-Dateien (.gitkeep bleibt). Gibt die Anzahl der übernommenen
    Fragmente zurück."""
    if not fragments_dir.is_dir() or not changelog_path.exists():
        return 0

    fragment_files = sorted(
        [f for f in fragments_dir.glob("*.md") if f.is_file() and f.name != ".gitkeep"]
    )
    if not fragment_files:
        return 0

    lines_to_insert = []
    for f in fragment_files:
        text = f.read_text(encoding="utf-8").strip()
        if text:
            for line in text.splitlines():
                s = line.strip()
                if s:
                    lines_to_insert.append(s)

    if not lines_to_insert:
        for f in fragment_files:
            f.unlink()
        return len(fragment_files)

    content = changelog_path.read_text(encoding="utf-8")
    heading = "### Geändert"
    if heading in content:
        idx = content.find(heading) + len(heading)
        next_nl = content.find("\n", idx)
        insert_pos = (next_nl + 1) if next_nl != -1 else len(content)
        insert_text = "\n".join(lines_to_insert) + "\n"
        new_content = content[:insert_pos] + insert_text + content[insert_pos:]
    else:
        unreleased = "## Unreleased"
        if unreleased in content:
            idx = content.find(unreleased) + len(unreleased)
            next_nl = content.find("\n", idx)
            insert_pos = (next_nl + 1) if next_nl != -1 else len(content)
            insert_text = "\n### Geändert\n" + "\n".join(lines_to_insert) + "\n"
            new_content = content[:insert_pos] + insert_text + content[insert_pos:]
        else:
            new_content = content.rstrip() + "\n\n## Unreleased\n\n### Geändert\n" + "\n".join(lines_to_insert) + "\n"

    changelog_path.write_text(new_content, encoding="utf-8")

    for f in fragment_files:
        f.unlink()

    return len(fragment_files)
