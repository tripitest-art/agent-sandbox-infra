from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "tests.yml"


def _job_block(text: str, name: str) -> str:
    """Zeilen des Jobs `name` (Schlüssel mit zwei Leerzeichen Einrückung) bis zum nächsten Job."""
    zeilen = text.splitlines()
    start = None
    for i, zeile in enumerate(zeilen):
        if zeile.rstrip() == f"  {name}:":
            start = i
            break
    assert start is not None, f"Job {name} fehlt in tests.yml"
    block = []
    for zeile in zeilen[start + 1:]:
        if zeile.startswith("  ") and not zeile.startswith("    ") and zeile.rstrip().endswith(":"):
            break
        block.append(zeile)
    return "\n".join(block)


def test_zweiter_job_faehrt_die_tests_als_root():
    block = _job_block(_WORKFLOW.read_text(encoding="utf-8"), "tests-als-root")
    assert "actions/checkout" in block
    assert "python-version: \"3.12\"" in block or "python-version: '3.12'" in block
    assert "sudo -E" in block
    assert "pytest" in block


def test_bestehender_job_bleibt_erhalten():
    block = _job_block(_WORKFLOW.read_text(encoding="utf-8"), "tests")
    assert "python -m pytest -q" in block
