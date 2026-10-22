"""Akzeptanztests #221: lokaler Pre-Flight-Health-Check für origin/main.

Lädt worker_tick.py per Pfad (Muster aus tests/test_worker_tick.py); subprocess.run,
gh, CLAIM_DIR und Zustand sind gefaked — kein Netzwerk, kein gh, kein Server.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _load():
    spec = importlib.util.spec_from_file_location("worker_tick_preflight", _SANDBOX / "worker_tick.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["worker_tick_preflight"] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load()

SHA = "0f2c6e8a4b1d3f5e7a9c0b2d4f6e8a1c3b5d7f9e"
KURZ = SHA[:7]
TEST_CMD = "python -m pytest -q"
JOB = ("issue5", "Auftragstext", None, None)
CI_GRUEN = [{"conclusion": "success", "headSha": "1234567abcdef000"}]


class FakeRun:
    """Ersatz für subprocess.run: Dispatch nach Befehlsinhalt, merkt alle Aufrufe.

    pytest_rc steuert den Exit-Code des Testlaufs, sha den von rev-parse; Einträge in
    fail (Teilstrings des Befehls) werfen CalledProcessError mit einem Text, der nie
    im Log auftauchen darf (Regel 5).
    """

    def __init__(self, pytest_rc=0, fail=(), sha=SHA):
        self.pytest_rc = pytest_rc
        self.fail = tuple(fail)
        self.sha = sha
        self.calls = []

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append((cmd, kwargs))
        # Dispatch über Befehlselemente, nie über den Join-String: Pytest-Temp-Pfade
        # enthalten „pytest" als Teilstring und würden Git-Aufrufe falsch treffen.
        for word in self.fail:
            if word in cmd:
                raise subprocess.CalledProcessError(1, cmd, output="", stderr="geheimer Text")
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=self.sha + "\n", stderr="")
        if "pytest" in cmd:
            return subprocess.CompletedProcess(cmd, self.pytest_rc, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def cmds(self):
        return [cmd for cmd, _ in self.calls]


class FakeGH:
    """gh-Ersatz: beantwortet nur `run list` (CI-Zustand von main, Muster aus #168)."""

    def __init__(self, runs):
        self.runs = runs
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("run", "list"):
            return self.runs
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _setup(monkeypatch, tmp_path, fake):
    """Standard-Umgebung: gefaktes subprocess.run, CLAIM_DIR/TEST_CMD kontrolliert."""
    monkeypatch.setattr(w.subprocess, "run", fake)
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "TEST_CMD", TEST_CMD)
    return tmp_path / "claims"


def _setup_jobs(monkeypatch, tmp_path):
    """find_job-Umgebung: Schutzklausel aus, CI grün, ein Issue-Kandidat, Zustand unter tmp_path."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(w, "gh", FakeGH(CI_GRUEN))
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "BASE_WORKDIR", str(tmp_path / "repo"))
    monkeypatch.setattr(w, "rework_job", lambda *a, **k: None)
    monkeypatch.setattr(w, "text_fix_job", lambda *a, **k: None)
    monkeypatch.setattr(w, "issue_job", lambda *a, **k: JOB)


def test_gruener_lauf_testet_im_worktree_und_schreibt_cache(monkeypatch, tmp_path):
    fake = FakeRun(pytest_rc=0)
    claims = _setup(monkeypatch, tmp_path, fake)
    assert w.check_main_health(str(tmp_path / "repo")) is True
    befehle = fake.cmds()
    assert any("fetch" in c and "origin" in c for c in befehle)
    # Testlauf im temporären Worktree von origin/main, nicht im Slot-Checkout
    add = [c for c in befehle if "worktree" in c and "add" in c]
    assert add, "git worktree add fehlt"
    wt = add[0][-2]
    pytest_calls = [kw for c, kw in fake.calls if "pytest" in c]
    assert pytest_calls and pytest_calls[0].get("cwd") == wt
    assert any("prune" in c for c in befehle)  # Worktree wird wieder aufgeräumt
    cache = claims / f"main_health_{SHA}.ok"
    assert cache.is_file() and cache.read_text(encoding="utf-8").strip() == SHA


def test_cache_treffer_vermeidet_testlauf(monkeypatch, tmp_path):
    fake = FakeRun(pytest_rc=1)  # würde rot liefern — darf aber gar nicht erst laufen
    claims = _setup(monkeypatch, tmp_path, fake)
    claims.mkdir(parents=True)
    (claims / f"main_health_{SHA}.ok").write_text(SHA + "\n", encoding="utf-8")
    assert w.check_main_health(str(tmp_path / "repo")) is True
    befehle = fake.cmds()
    assert not any("pytest" in c for c in befehle)
    assert not any("worktree" in c and "add" in c for c in befehle)


def test_roter_testlauf_liefert_false_ohne_cache(monkeypatch, tmp_path, capsys):
    fake = FakeRun(pytest_rc=1)
    claims = _setup(monkeypatch, tmp_path, fake)
    assert w.check_main_health(str(tmp_path / "repo")) is False
    assert not (claims / f"main_health_{SHA}.ok").exists()
    out = capsys.readouterr().out
    assert "Warnung" in out and KURZ in out and "rot" in out
    assert any("prune" in c for c in fake.cmds())  # auch bei Rot wird aufgeräumt


def test_git_fehler_blockiert_nicht_und_verraet_keine_details(monkeypatch, tmp_path, capsys):
    fake = FakeRun(fail=("rev-parse",))
    _setup(monkeypatch, tmp_path, fake)
    assert w.check_main_health(str(tmp_path / "repo")) is True  # unbekannt ≠ rot
    out = capsys.readouterr().out
    assert "Warnung" in out
    assert "geheimer Text" not in out  # Regel 5: nur Klasse, nie der Ausnahmetext


def test_lokal_rotes_main_sperrt_auftraege_und_meldet_einmal(monkeypatch, tmp_path, capsys):
    fake = FakeRun(pytest_rc=1)
    _setup(monkeypatch, tmp_path, fake)
    _setup_jobs(monkeypatch, tmp_path)
    state = {}
    assert w.find_job(state) is None
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1 and KURZ in zeilen[0] and "main" in zeilen[0]
    assert w.find_job(state) is None
    assert [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z] == []


def test_gruenes_lokales_main_gibt_auftrag_wieder_frei(monkeypatch, tmp_path, capsys):
    fake = FakeRun(pytest_rc=1)
    _setup(monkeypatch, tmp_path, fake)
    _setup_jobs(monkeypatch, tmp_path)
    state = {}
    assert w.find_job(state) is None
    fake.pytest_rc = 0  # Umgebung repariert, derselbe SHA
    assert w.find_job(state) == JOB
    assert not any(k.startswith("_mainrotlokal") for k in state)
    capsys.readouterr()
    fake.pytest_rc = 1
    fake.sha = "b" * 40  # neuer Commit auf main: ohne Cache wird neu getestet
    assert w.find_job(state) is None
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert zeilen and "b" * 7 in zeilen[0]
