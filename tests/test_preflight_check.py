"""Unit-Tests #221: lokaler Pre-Flight-Health-Check (check_main_health/main_is_red_local).

Lädt `worker_tick.py` per Pfad; `subprocess.run` und `gh` sind gefaked — kein Netzwerk, kein
Server, keine echten Git-Aufrufe. Ergänzt die eingefrorenen Akzeptanztests aus
`tests/acceptance/test_issue_221.py` um die Randfälle aus dem Issue.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_preflight_unit", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_preflight_unit"] = w
_spec.loader.exec_module(w)

SHA = "0f2c6e8a4b1d3f5e7a9c0b2d4f6e8a1c3b5d7f9e"
KURZ = SHA[:7]
TEST_CMD = "python -m pytest -q"
JOB = ("issue5", "Auftragstext", None, None)


class FakeRun:
    """subprocess.run-Ersatz: je Befehl ein eigener Exit-Code oder eine Ausnahme."""

    def __init__(self, fetch_rc=0, add_rc=0, pytest_rc=0, sha=SHA, raises=()):
        self.fetch_rc = fetch_rc
        self.add_rc = add_rc
        self.pytest_rc = pytest_rc
        self.sha = sha
        self.raises = set(raises)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append((cmd, kwargs))
        # Dispatch über Befehlselemente: Pytest-Temp-Pfade enthalten „pytest“ als Teilstring.
        if "fetch" in cmd:
            if "fetch" in self.raises:
                raise OSError("kaputt")
            return subprocess.CompletedProcess(cmd, self.fetch_rc, stdout="", stderr="")
        if "rev-parse" in cmd:
            if "rev-parse" in self.raises:
                raise subprocess.CalledProcessError(1, cmd, stderr="geheimer Text")
            return subprocess.CompletedProcess(cmd, 0, stdout=(self.sha + "\n") if self.sha else "")
        if "worktree" in cmd and "add" in cmd:
            if "add" in self.raises:
                raise OSError("kaputt")
            return subprocess.CompletedProcess(cmd, self.add_rc, stdout="", stderr="")
        if "prune" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "pytest" in cmd:
            if "pytest" in self.raises:
                raise subprocess.TimeoutExpired(cmd, 600)
            return subprocess.CompletedProcess(cmd, self.pytest_rc, stdout="", stderr="")
        raise AssertionError(f"unerwarteter Befehl: {cmd}")

    def cmds(self):
        return [cmd for cmd, _ in self.calls]


class FakeGH:
    """gh-Ersatz für `run list` (CI-Zustand von main, Muster aus #168)."""

    def __init__(self, runs):
        self.runs = runs
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("run", "list"):
            return self.runs
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _mount(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(w.subprocess, "run", fake)
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "TEST_CMD", TEST_CMD)
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    return tmp_path / "claims"


def _mount_jobs(monkeypatch, tmp_path, fake, runs, job=JOB):
    """find_job-Umgebung: Schutzklausel aus, gefaktes gh/subprocess, Nacharbeit leer."""
    _mount(monkeypatch, tmp_path, fake)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(w, "gh", FakeGH(runs))
    monkeypatch.setattr(w, "BASE_WORKDIR", str(tmp_path / "repo"))
    monkeypatch.setattr(w, "rework_job", lambda *a, **k: None)
    monkeypatch.setattr(w, "text_fix_job", lambda *a, **k: None)
    monkeypatch.setattr(w, "issue_job", lambda *a, **k: job)
    return tmp_path / "repo"


def _worktree_tmp(fake):
    add = [c for c in fake.cmds() if "worktree" in c and "add" in c]
    assert add, "git worktree add fehlt"
    return add[0][-2]


def test_fetch_exitcode_blockiert_nicht(monkeypatch, tmp_path, capsys):
    fake = FakeRun(fetch_rc=128)
    _mount(monkeypatch, tmp_path, fake)
    assert w.check_main_health(str(tmp_path / "repo")) is True
    out = capsys.readouterr().out
    assert "Warnung" in out and "fetch fehlgeschlagen (Exit 128)" in out
    assert not any("pytest" in c for c in fake.cmds())  # ohne fetch kein Testlauf


def test_fetch_oserror_blockiert_nicht(monkeypatch, tmp_path, capsys):
    fake = FakeRun(raises=("fetch",))
    _mount(monkeypatch, tmp_path, fake)
    assert w.check_main_health(str(tmp_path / "repo")) is True
    out = capsys.readouterr().out
    assert "Warnung" in out and "OSError" in out


def test_fetch_timeout_blockiert_nicht(monkeypatch, tmp_path, capsys):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    fake = FakeRun()
    _mount(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(w.subprocess, "run", boom)
    assert w.check_main_health(str(tmp_path / "repo")) is True
    out = capsys.readouterr().out
    assert "Warnung" in out and "TimeoutExpired" in out


def test_worktree_add_exitcode_raeumt_auf_und_blockiert_nicht(monkeypatch, tmp_path, capsys):
    fake = FakeRun(add_rc=128)
    _mount(monkeypatch, tmp_path, fake)
    assert w.check_main_health(str(tmp_path / "repo")) is True
    tmp = _worktree_tmp(fake)
    assert not os.path.exists(tmp), "temporärer Worktree blieb liegen"
    assert any("prune" in c for c in fake.cmds())
    out = capsys.readouterr().out
    assert "Warnung" in out and "worktree add fehlgeschlagen (Exit 128)" in out


def test_worktree_add_oserror_raeumt_auf_und_blockiert_nicht(monkeypatch, tmp_path, capsys):
    fake = FakeRun(raises=("add",))
    _mount(monkeypatch, tmp_path, fake)
    assert w.check_main_health(str(tmp_path / "repo")) is True
    tmp = _worktree_tmp(fake)
    assert not os.path.exists(tmp), "temporärer Worktree blieb liegen"
    out = capsys.readouterr().out
    assert "Warnung" in out and "OSError" in out


def test_timeout_im_testlauf_blockiert_nicht(monkeypatch, tmp_path, capsys):
    fake = FakeRun(raises=("pytest",))
    claims = _mount(monkeypatch, tmp_path, fake)
    assert w.check_main_health(str(tmp_path / "repo")) is True
    tmp = _worktree_tmp(fake)
    assert not os.path.exists(tmp)
    assert not (claims / f"main_health_{SHA}.ok").exists()
    out = capsys.readouterr().out
    assert "Warnung" in out and "Testlauf fehlgeschlagen (TimeoutExpired)" in out


def test_oserror_beim_cache_schreiben_bleibt_gruen(monkeypatch, tmp_path, capsys):
    fake = FakeRun(pytest_rc=0)
    claims = _mount(monkeypatch, tmp_path, fake)
    claims.write_text("kein Verzeichnis", encoding="utf-8")  # makedirs scheitert mit OSError
    assert w.check_main_health(str(tmp_path / "repo")) is True
    out = capsys.readouterr().out
    assert "Warnung" in out and "Cache nicht schreibbar" in out
    assert claims.is_file()  # die Datei bleibt unangetastet


def test_main_sha_leere_ausgabe_liefert_none(monkeypatch, tmp_path, capsys):
    fake = FakeRun(sha="")
    _mount(monkeypatch, tmp_path, fake)
    assert w.main_sha(str(tmp_path / "repo")) is None
    capsys.readouterr()


def test_main_sha_verraet_keinen_ausnahmetext(monkeypatch, tmp_path, capsys):
    fake = FakeRun(raises=("rev-parse",))
    _mount(monkeypatch, tmp_path, fake)
    assert w.main_sha(str(tmp_path / "repo")) is None
    out = capsys.readouterr().out
    assert "CalledProcessError" in out and "geheimer Text" not in out


def test_ohne_kandidat_kein_lokaler_check(monkeypatch, tmp_path):
    fake = FakeRun(pytest_rc=1)
    gh = FakeGH([])
    _mount_jobs(monkeypatch, tmp_path, fake, [], job=None)
    monkeypatch.setattr(w, "gh", gh)
    assert w.find_job({}) is None
    assert fake.calls == []  # kein fetch, kein Worktree, kein Testlauf
    assert gh.calls == []


def test_ci_rotes_main_short_circuitet_vor_dem_lokalen_check(monkeypatch, tmp_path, capsys):
    fake = FakeRun(pytest_rc=0)
    rot = [{"conclusion": "failure", "headSha": "1234567abcdef000"}]
    _mount_jobs(monkeypatch, tmp_path, fake, rot)
    state = {}
    assert w.find_job(state) is None
    assert fake.calls == [], "lokaler Check lief trotz CI-rotem main"
    out = capsys.readouterr().out
    assert "⚠ Stephan:" in out and "1234567" in out


def test_schutzklausel_verhindert_check_unter_pytest(monkeypatch, tmp_path):
    fake = FakeRun(pytest_rc=1)  # würde rot liefern
    _mount(monkeypatch, tmp_path, fake)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_preflight_check.py::x (call)")
    assert w.main_is_red_local({}) is False
    assert fake.calls == []


def test_lokal_rot_sperrt_nur_issue_auftraege(monkeypatch, tmp_path):
    fake = FakeRun(pytest_rc=1)
    _mount_jobs(monkeypatch, tmp_path, fake, [{"conclusion": "success", "headSha": "1234567"}])
    state = {}
    assert w.find_job(state) is None
    key = f"_mainrotlokal:{w.REPO}"
    assert state.get(key) == SHA
    assert (tmp_path / "state.json").is_file()


def test_wieder_gruen_loescht_meldemarke(monkeypatch, tmp_path):
    fake = FakeRun(pytest_rc=1)
    _mount_jobs(monkeypatch, tmp_path, fake, [{"conclusion": "success", "headSha": "1234567"}])
    state = {}
    assert w.find_job(state) is None
    assert f"_mainrotlokal:{w.REPO}" in state
    fake.pytest_rc = 0
    assert w.find_job(state) == JOB
    assert f"_mainrotlokal:{w.REPO}" not in state


@pytest.mark.parametrize("sha", [None, ""])
def test_unbekannter_sha_blockiert_nicht(monkeypatch, tmp_path, sha):
    fake = FakeRun(sha=sha)
    claims = _mount(monkeypatch, tmp_path, fake)
    assert w.check_main_health(str(tmp_path / "repo")) is True
    assert not any("worktree" in c and "add" in c for c in fake.cmds())
    assert not claims.exists(), "ohne SHA darf keine Cache-Marke entstehen"
