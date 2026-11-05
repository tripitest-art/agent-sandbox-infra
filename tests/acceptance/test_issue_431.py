import importlib.util
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_rote_tests", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_rote_tests"] = w
_spec.loader.exec_module(w)

SHA = "0f2c6e8a4b1d3f5e7a9c0b2d4f6e8a1c3b5d7f9e"
KURZ = SHA[:7]
JOB = ("issue5", "Auftragstext", None, None)
AUSGABE = (
    "..F.E\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_a.py::test_eins - assert 'geheim' == 'x'\n"
    "ERROR tests/test_b.py::test_zwei[p1] - ModuleNotFoundError: No module named 'psutil'\n"
    "FAILED tests/test_a.py::test_eins - doppelt\n"
    "FAILED kaputt name mit leerzeichen\n"
    "2 failed, 1 error in 3.1s\n"
)


class FakeRun:
    """subprocess.run-Ersatz wie in test_issue_221.py; der Testlauf liefert `pytest_out`."""

    def __init__(self, pytest_rc=1, pytest_out=AUSGABE):
        self.pytest_rc, self.pytest_out = pytest_rc, pytest_out
        self.calls = []

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=SHA + "\n", stderr="")
        if "pytest" in cmd:
            return subprocess.CompletedProcess(cmd, self.pytest_rc, stdout=self.pytest_out, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


class FakeGH:
    def __call__(self, *args):
        if args[:2] == ("run", "list"):
            return [{"conclusion": "success", "headSha": "1234567abcdef000"}]
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _setup(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(w.subprocess, "run", fake)
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "TEST_CMD", "python -m pytest -q")


def test_rote_tests_nimmt_nur_saubere_namen():
    assert w.rote_tests(AUSGABE) == ["tests/test_a.py::test_eins", "tests/test_b.py::test_zwei[p1]"]
    assert w.rote_tests(AUSGABE, limit=1) == ["tests/test_a.py::test_eins"]
    assert w.rote_tests("") == [] and w.rote_tests(AUSGABE, limit=0) == []
    viele = "".join(f"FAILED tests/t.py::test_{i} - x\n" for i in range(9))
    assert len(w.rote_tests(viele)) == 5


def test_roter_lauf_nennt_die_namen_ohne_meldungstext(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, FakeRun())
    assert w.check_main_health(str(tmp_path / "repo")) is False
    assert w.LETZTE_ROTE_TESTS == ["tests/test_a.py::test_eins", "tests/test_b.py::test_zwei[p1]"]
    out = capsys.readouterr().out
    assert f"Lokale Tests auf origin/main ({KURZ}) rot (Exit 1)." in out, "bisherige Warnzeile bleibt"
    assert (f"Lokale Tests auf origin/main ({KURZ}): rot sind "
            "tests/test_a.py::test_eins, tests/test_b.py::test_zwei[p1].") in out
    assert "geheim" not in out and "psutil" not in out, "Meldungstexte gehören nicht ins Log"


def test_gruener_lauf_leert_die_liste(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, FakeRun())
    assert w.check_main_health(str(tmp_path / "repo")) is False
    _setup(monkeypatch, tmp_path, FakeRun(pytest_rc=0, pytest_out="3 passed\n"))
    assert w.check_main_health(str(tmp_path / "repo")) is True
    assert w.LETZTE_ROTE_TESTS == []


def test_roter_lauf_ohne_namen_schreibt_keine_zusatzzeile(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, FakeRun(pytest_out="Interrupted: 1 error during collection\n"))
    assert w.check_main_health(str(tmp_path / "repo")) is False
    assert w.LETZTE_ROTE_TESTS == []
    assert "rot sind" not in capsys.readouterr().out


def test_stephan_zeile_nennt_die_roten_tests(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, FakeRun())
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(w, "gh", FakeGH())
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "BASE_WORKDIR", str(tmp_path / "repo"))
    monkeypatch.setattr(w, "rework_job", lambda *a, **k: None)
    monkeypatch.setattr(w, "text_fix_job", lambda *a, **k: None)
    monkeypatch.setattr(w, "issue_job", lambda *a, **k: JOB)
    state = {}
    assert w.find_job(state) is None
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1
    assert zeilen[0].endswith(f"⚠ Stephan: Lokale Tests auf origin/main ({KURZ}) rot; keine neuen Issue-Aufträge. "
                              "Rot: tests/test_a.py::test_eins, tests/test_b.py::test_zwei[p1].")
    assert w.find_job(state) is None
    assert [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z] == [], "eine Zeile je Commit"
