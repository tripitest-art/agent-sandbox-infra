"""Akzeptanztests für Issue #<N>: die PR-Vorlage kommt aus origin/main, nicht aus dem Arbeitsbaum."""

import importlib.util
import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("worker_tick", ROOT / "sandbox" / "worker_tick.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

GIT_VORLAGE = "VORLAGE AUS ORIGIN/MAIN\n"
LOKAL_VERALTET = "LOKAL VERALTET\n"


class _Out:
    def __init__(self, stdout):
        self.stdout = stdout


def _frisch(monkeypatch):
    """Zwischenspeicher leeren, damit jeder Test bei null anfängt."""
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", None, raising=False)


def test_vorlage_kommt_aus_origin_main(monkeypatch):
    _frisch(monkeypatch)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return _Out(GIT_VORLAGE)

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    monkeypatch.setattr(w, "open", lambda *a, **kw: io.StringIO(LOKAL_VERALTET), raising=False)
    assert w.pr_template() == GIT_VORLAGE
    assert calls and calls[0][:3] == ["git", "-C", w.WORKDIR]
    assert f"origin/main:{w.PR_TEMPLATE}" in calls[0]


def test_vorlage_meldet_wenn_nichts_geht(monkeypatch, capsys):
    _frisch(monkeypatch)

    def boom_run(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    def boom_open(*a, **kw):
        raise OSError("keine Vorlage")

    monkeypatch.setattr(w.subprocess, "run", boom_run)
    monkeypatch.setattr(w, "open", boom_open, raising=False)
    assert w.pr_template() == ""
    assert "WARNUNG" in capsys.readouterr().out


def test_vorlage_wird_einmal_geholt(monkeypatch):
    _frisch(monkeypatch)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return _Out(GIT_VORLAGE)

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    assert w.pr_template() == GIT_VORLAGE
    assert w.pr_template() == GIT_VORLAGE
    assert len(calls) == 1


def test_projekttest_haengt_nicht_am_rechner():
    # Sonst schlägt die Zusicherung auf dem Server fehl, wo der Klon samt Vorlage liegt.
    text = (ROOT / "tests" / "test_projects.py").read_text(encoding="utf-8")
    assert 'setattr(w, "pr_template"' in text
