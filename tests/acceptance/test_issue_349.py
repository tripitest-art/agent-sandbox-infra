import importlib.util
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "scripts" / "issuecheck.py"
_spec = importlib.util.spec_from_file_location("issuecheck_rolle_im_repo", _SRC)
ic = importlib.util.module_from_spec(_spec)
sys.modules["issuecheck_rolle_im_repo"] = ic
_spec.loader.exec_module(ic)

# Der Block lädt sein Modul wie echte Akzeptanztests über parents[2] und hängt von der uid ab.
UID_BLOCK = (
    "import importlib.util\nimport sys\nfrom pathlib import Path\n\n"
    "_P = Path(__file__).resolve().parents[2] / 'sandbox' / 'neu.py'\n"
    "_s = importlib.util.spec_from_file_location('neu_rolle', _P)\n"
    "neu = importlib.util.module_from_spec(_s)\nsys.modules['neu_rolle'] = neu\n_s.loader.exec_module(neu)\n\n\n"
    "def test_nicht_root():\n    assert neu.laufnutzer() != 0\n"
)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "sandbox").mkdir(parents=True)
    (repo / "tests" / "acceptance").mkdir(parents=True)
    (repo / "sandbox" / "neu.py").write_text(
        "import os\n\n\ndef laufnutzer() -> int:\n    return os.getuid()\n", encoding="utf-8")
    return repo


def _issue(block):
    return ("### Ziel\n\nx\n\n### Betroffene Dateien\n\n- `sandbox/neu.py`\n\n"
            "### Schnittstellen\n\n```python\ndef laufnutzer() -> int:\n    pass\n```\n\n"
            "### Nicht Teil dieser Aufgabe\n\n- nichts\n\n"
            f"### Akzeptanztests\n\n```python\n{block}```\n\n"
            "### Fertig, wenn\n\n- [ ] grün\n\n### Merge-Modus\n\nautomatisch nach Review\n\n"
            "### Kontext\n\nPreflight.\n")


def test_rollenlauf_findet_das_repo_ueber_parents_2(tmp_path, monkeypatch):
    monkeypatch.setattr(ic.os, "getuid", lambda: 1000)
    repo = _repo(tmp_path)
    findings, ausgabe = ic.check_rollen(_issue(UID_BLOCK), repo)
    assert "FileNotFoundError" not in ausgabe
    assert "laufnutzerabhaengig" in [f.code for f in findings if f.level == "fehler"]
    assert not list((repo / "tests" / "acceptance").glob("test_issue_preflight*"))


def test_sammelfehler_im_rollenlauf_wird_gemeldet(tmp_path, monkeypatch):
    monkeypatch.setattr(ic.os, "getuid", lambda: 1000)
    repo = _repo(tmp_path)
    block = "import modul_das_es_nicht_gibt_xyz\n\n\ndef test_a():\n    pass\n"
    findings, _ = ic.check_rollen(_issue(block), repo)
    warnungen = [f for f in findings if f.code == "root-lauf-nicht-ausfuehrbar"]
    assert warnungen and warnungen[0].level == "warnung"
    assert warnungen[0].text.startswith("Der zweite Lauf konnte den Akzeptanztest nicht ausführen")
    assert not list((repo / "tests" / "acceptance").glob("test_issue_preflight*"))
