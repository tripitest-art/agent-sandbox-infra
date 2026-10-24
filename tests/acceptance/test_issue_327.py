import importlib.util
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "scripts" / "issuecheck.py"
_spec = importlib.util.spec_from_file_location("issuecheck_rollen", _SRC)
ic = importlib.util.module_from_spec(_spec)
sys.modules["issuecheck_rollen"] = ic
_spec.loader.exec_module(ic)


def _repo(tmp_path, nutzerabhaengig: bool):
    repo = tmp_path / "repo"
    (repo / "sandbox").mkdir(parents=True)
    (repo / "tests").mkdir()
    if nutzerabhaengig:
        quelle = "import os\n\n\ndef laufnutzer() -> int:\n    return os.getuid()\n"
    else:
        quelle = "def laufnutzer() -> int:\n    return 42\n"
    (repo / "sandbox" / "neu.py").write_text(quelle, encoding="utf-8")
    return repo


def _issue(block: str) -> str:
    return (
        "### Ziel\n\nx\n\n"
        "### Betroffene Dateien\n\n- `sandbox/neu.py`\n- `tests/acceptance/test_issue_<N>.py`\n\n"
        "### Schnittstellen\n\n```python\ndef laufnutzer() -> int:\n    pass\n```\n\n"
        "### Nicht Teil dieser Aufgabe\n\n- nichts\n\n"
        f"### Akzeptanztests\n\n```python\n{block}```\n\n"
        "### Fertig, wenn\n\n- [ ] grün\n\n"
        "### Merge-Modus\n\nautomatisch nach Review\n\n"
        "### Kontext\n\nPlaner-Preflight.\n"
    )


def test_failed_ids_liest_nur_failed_zeilen():
    ausgabe = (
        "FAILED tests/test_a.py::test_b - AssertionError: nein\n"
        "2 failed, 1 passed in 0.10s\n"
        "FAILED tests/test_c.py::test_d\n"
    )
    assert ic._failed_ids(ausgabe) == {"tests/test_a.py::test_b", "tests/test_c.py::test_d"}
    assert ic._failed_ids("3 passed in 0.01s") == set()


def test_check_rollen_meldet_laufnutzerabhaengigen_test(tmp_path):
    block = (
        "from sandbox.neu import laufnutzer\n\n\n"
        "def test_nicht_root():\n    assert laufnutzer() != 0\n"
    )
    repo = _repo(tmp_path, nutzerabhaengig=True)
    findings, _ = ic.check_rollen(_issue(block), repo)
    codes = [f.code for f in findings if f.level == "fehler"]
    assert "laufnutzerabhaengig" in codes
    assert any("test_nicht_root" in f.text for f in findings if f.code == "laufnutzerabhaengig")


def test_check_rollen_ohne_befund_bei_unabhaengigem_test(tmp_path):
    block = (
        "from sandbox.neu import laufnutzer\n\n\n"
        "def test_konstant():\n    assert laufnutzer() == 42\n"
    )
    repo = _repo(tmp_path, nutzerabhaengig=False)
    findings, _ = ic.check_rollen(_issue(block), repo)
    assert not [f for f in findings if f.code == "laufnutzerabhaengig"]


def test_validate_reicht_den_befund_durch(tmp_path):
    block = (
        "from sandbox.neu import laufnutzer\n\n\n"
        "def test_nicht_root():\n    assert laufnutzer() != 0\n"
    )
    repo = _repo(tmp_path, nutzerabhaengig=True)
    findings, _ = ic.validate(_issue(block), repo)
    assert "laufnutzerabhaengig" in [f.code for f in findings if f.level == "fehler"]
