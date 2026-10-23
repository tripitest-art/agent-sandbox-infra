import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_no_progress", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_no_progress"] = w
_spec.loader.exec_module(w)

T0 = 1760880000.0
MIN = 60.0
SHA = "a" * 40


class FakeProc:
    def __init__(self):
        self.beendet = 0

    def terminate(self):
        self.beendet += 1


def _git(monkeypatch, sha=SHA, status="", rc=0, wirft=None):
    aufrufe = []

    def fake_run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        aufrufe.append((cmd, kw))
        if wirft:
            raise wirft
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, rc, stdout=sha + "\n", stderr="")
        if "status" in cmd:
            return subprocess.CompletedProcess(cmd, rc, stdout=status, stderr="")
        raise AssertionError(f"unerwarteter Befehl: {cmd}")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    return aufrufe


def test_vorgabe_ist_fuenfzehn_minuten():
    assert w.NO_PROGRESS_MINUTEN == 15


def test_zustand_besteht_aus_head_und_status(monkeypatch, tmp_path):
    aufrufe = _git(monkeypatch, status="?? neu.txt\nA  b.py\n")
    zustand = w.workdir_zustand(str(tmp_path))
    assert zustand == SHA + "\n" + "?? neu.txt\nA  b.py"
    assert [c for c, _ in aufrufe] == [["git", "rev-parse", "HEAD"], ["git", "status", "--porcelain"]]
    assert all(kw.get("cwd") == str(tmp_path) for _, kw in aufrufe)


def test_neuer_commit_oder_neue_datei_aendert_den_zustand(monkeypatch, tmp_path):
    _git(monkeypatch)
    sauber = w.workdir_zustand(str(tmp_path))
    _git(monkeypatch, sha="b" * 40)
    assert w.workdir_zustand(str(tmp_path)) != sauber
    _git(monkeypatch, status="?? neu.txt\n")
    assert w.workdir_zustand(str(tmp_path)) != sauber


@pytest.mark.parametrize("fehler", [{"rc": 1}, {"wirft": OSError("kaputt")}, {"wirft": subprocess.TimeoutExpired("git", 5)}])
def test_zustand_ist_none_bei_jedem_fehler_und_wirft_nie(monkeypatch, tmp_path, fehler):
    _git(monkeypatch, **fehler)
    assert w.workdir_zustand(str(tmp_path)) is None


@pytest.mark.parametrize("vorher, jetzt, laufzeit, erwartet", [
    ("z", "z", 15 * MIN, True),
    ("z", "z", 15 * MIN - 1, False),
    ("z", "z", 40 * MIN, True),
    ("z", "anders", 40 * MIN, False),
    (None, None, 40 * MIN, False),
    ("z", None, 40 * MIN, False),
    (None, "z", 40 * MIN, False),
])
def test_ohne_fortschritt(vorher, jetzt, laufzeit, erwartet):
    assert w.ohne_fortschritt(vorher, jetzt, laufzeit) is erwartet


def test_watchdog_beendet_den_agenten_und_loggt_einmal(monkeypatch, tmp_path, capsys):
    _git(monkeypatch)
    vorher = w.workdir_zustand(str(tmp_path))
    proc = FakeProc()
    assert w.watchdog_pruefen(proc, vorher, T0, str(tmp_path), jetzt=T0 + 16 * MIN) is True
    assert proc.beendet == 1
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "WARNUNG: Agent hat seit 16 Minuten nichts geändert" in z]
    assert len(zeilen) == 1


def test_watchdog_laesst_den_agenten_vor_ablauf_in_ruhe(monkeypatch, tmp_path, capsys):
    _git(monkeypatch)
    vorher = w.workdir_zustand(str(tmp_path))
    proc = FakeProc()
    assert w.watchdog_pruefen(proc, vorher, T0, str(tmp_path), jetzt=T0 + 14 * MIN) is False
    assert proc.beendet == 0
    assert "WARNUNG" not in capsys.readouterr().out


def test_watchdog_laesst_den_agenten_in_ruhe_wenn_er_etwas_geaendert_hat(monkeypatch, tmp_path):
    _git(monkeypatch)
    vorher = w.workdir_zustand(str(tmp_path))
    _git(monkeypatch, status="?? neu.txt\n")
    proc = FakeProc()
    assert w.watchdog_pruefen(proc, vorher, T0, str(tmp_path), jetzt=T0 + 60 * MIN) is False
    assert proc.beendet == 0


def test_watchdog_beendet_nichts_wenn_der_zustand_unbekannt_ist(monkeypatch, tmp_path):
    _git(monkeypatch, rc=1)
    proc = FakeProc()
    assert w.watchdog_pruefen(proc, None, T0, str(tmp_path), jetzt=T0 + 60 * MIN) is False
    assert proc.beendet == 0
