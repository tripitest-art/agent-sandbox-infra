import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("issuecheck_pfad", _ROOT / "scripts" / "issuecheck.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["issuecheck_pfad"] = mod
    spec.loader.exec_module(mod)
    return mod


ic = _load()


def _repo(tmp_path, mit_acceptance=True):
    (tmp_path / "sandbox").mkdir()
    (tmp_path / "sandbox" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    if mit_acceptance:
        (tmp_path / "tests" / "acceptance").mkdir(parents=True)
    return tmp_path


def _issue(code):
    return f"### Akzeptanztests\n\n```python\n{code}\n```\n"


PFAD_TEST = """from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUELLE = (ROOT / "sandbox" / "mod.py").read_text(encoding="utf-8")


def test_g_fehlt_noch():
    assert "def g" in QUELLE
"""


def _codes(findings):
    return {f.code for f in findings}


def test_repo_pfad_ueber_parents_findet_das_repo(tmp_path):
    repo = _repo(tmp_path)
    findings, output = ic.check_red(_issue(PFAD_TEST), repo)
    assert "FileNotFoundError" not in output
    assert "AssertionError" in output
    assert _codes(findings) == set()


def test_datei_wird_danach_geloescht(tmp_path):
    repo = _repo(tmp_path)
    ic.check_red(_issue(PFAD_TEST), repo)
    assert list((repo / "tests" / "acceptance").iterdir()) == []


def test_keep_tmp_laesst_die_datei_liegen(tmp_path):
    repo = _repo(tmp_path)
    ic.check_red(_issue(PFAD_TEST), repo, keep_tmp=True)
    liegen = list((repo / "tests" / "acceptance").glob("test_issue_preflight_*.py"))
    assert len(liegen) == 1


def test_sammlungsfehler_ohne_import_ist_ein_befund(tmp_path):
    repo = _repo(tmp_path)
    code = 'raise FileNotFoundError("geht nicht")\n\n\ndef test_x():\n    assert False\n'
    findings, output = ic.check_red(_issue(code), repo)
    assert "sammlungsfehler" in _codes(findings)
    assert all(f.level == "fehler" for f in findings if f.code == "sammlungsfehler")


def test_fehlender_import_bleibt_ein_erwarteter_rot_grund(tmp_path):
    repo = _repo(tmp_path)
    code = "import modul_das_es_nicht_gibt\n\n\ndef test_x():\n    assert modul_das_es_nicht_gibt\n"
    findings, output = ic.check_red(_issue(code), repo)
    assert "sammlungsfehler" not in _codes(findings)
    assert "schon-gruen" not in _codes(findings)


def test_ohne_acceptance_ordner_wird_nichts_im_repo_angelegt(tmp_path):
    repo = _repo(tmp_path, mit_acceptance=False)
    findings, output = ic.check_red(_issue("def test_x():\n    assert False\n"), repo)
    assert _codes(findings) == set()
    assert not (repo / "tests").exists()
