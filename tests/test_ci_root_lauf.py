"""Der CI-Job `tests-als-root` spiegelt die Umgebung des pre-push-Hooks (#328).

Geprüft wird nur der Text der Workflow-Datei: die CI installiert ausschließlich pytest, PyYAML
steht nicht zur Verfügung.
"""
from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "tests.yml"


def _job_block(text: str, name: str) -> str:
    """Zeilen des Jobs `name` (Schlüssel mit zwei Leerzeichen Einrückung) bis zum nächsten Job.
    Die Einrückung ist die einzige Struktur, auf die sich der Test stützt."""
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


def test_zweiter_job_faehrt_die_tests_als_root() -> None:
    """Der Job `tests-als-root` checkt aus, richtet Python 3.12 ein und startet pytest per sudo."""
    block = _job_block(_WORKFLOW.read_text(encoding="utf-8"), "tests-als-root")
    assert "runs-on: ubuntu-latest" in block
    assert "actions/checkout" in block
    assert "actions/setup-python" in block
    assert 'python-version: "3.12"' in block
    assert "sudo -E" in block and "pytest" in block


def test_bestehender_job_bleibt_erhalten() -> None:
    """Der Job `tests` enthält weiterhin `python -m pytest -q` (Pflicht-Check, #168/#221)."""
    block = _job_block(_WORKFLOW.read_text(encoding="utf-8"), "tests")
    assert "python -m pytest -q" in block
    assert "sudo" not in block, "der Pflicht-Check läuft weiter als Runner-Nutzer"
