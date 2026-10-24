"""Tests für die Push-Diagnose in open_pr() (#329).

Ein abgelehnter Takt-Push nennt im Log nur die Node-IDs der roten Tests. Der Helfer filtert
die Ausgabe des Push-Befehls auf `FAILED `-Zeilen und den Teil vor ` - `, damit weder
Fehlertexte noch Secrets aus derselben Zeile im Log landen (Regel 5).
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_push_diagnose_tests", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_push_diagnose_tests"] = w
_spec.loader.exec_module(w)


def test_ohne_failed_zeile_leer():
    assert w.rote_tests_aus_push_ausgabe("") == []
    assert w.rote_tests_aus_push_ausgabe("remote: rejected\npre-push: Tests fehlgeschlagen\n") == []


def test_nennt_nur_node_ids_ohne_fehlertext():
    ausgabe = (
        "pre-push: Tests fehlgeschlagen\n"
        "FAILED tests/test_a.py::test_b - AssertionError: token ghp_GEHEIM\n"
        "FAILED tests/test_c.py::test_d\n"
    )
    assert w.rote_tests_aus_push_ausgabe(ausgabe) == [
        "tests/test_a.py::test_b",
        "tests/test_c.py::test_d",
    ]


def test_entdoppelt_und_haelt_die_reihenfolge():
    ausgabe = (
        "FAILED tests/t1.py::test_1\n"
        "FAILED tests/t0.py::test_0\n"
        "FAILED tests/t1.py::test_1\n"
    )
    assert w.rote_tests_aus_push_ausgabe(ausgabe) == [
        "tests/t1.py::test_1",
        "tests/t0.py::test_0",
    ]


def test_limit_begrenzt_und_null_ist_leer():
    ausgabe = "".join(f"FAILED tests/t{i}.py::test_{i}\n" for i in range(5))
    assert w.rote_tests_aus_push_ausgabe(ausgabe, limit=2) == [
        "tests/t0.py::test_0",
        "tests/t1.py::test_1",
    ]
    assert w.rote_tests_aus_push_ausgabe(ausgabe, limit=0) == []
    assert w.rote_tests_aus_push_ausgabe(ausgabe, limit=-1) == []


def test_lange_node_id_wird_auf_120_zeichen_gekuerzt():
    lang = "tests/" + "x" * 200 + ".py::test_lang"
    (name,) = w.rote_tests_aus_push_ausgabe(f"FAILED {lang} - AssertionError\n")
    assert len(name) == 120
    assert name == lang[:120]


def test_zeile_ohne_namen_zaehlt_nicht():
    assert w.rote_tests_aus_push_ausgabe("FAILED \nFAILED   - nur ein Fehler\n") == []


def test_open_pr_loggt_hinweis_ohne_failed_zeile(monkeypatch, tmp_path, capsys):
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(w, "WORKDIR", str(wt))
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", "## Vorlage\n")
    monkeypatch.setattr(w, "alarm", lambda *a, **k: None)

    def fake_gh(*args):
        if args[:2] == ("pr", "list"):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    def fake_run(cmd, **kw):
        if list(cmd[:2]) == ["git", "push"]:
            return subprocess.CompletedProcess(cmd, 1, "remote: rejected\n", "")
        if list(cmd[:3]) == ["git", "rev-list", "--count"]:
            return subprocess.CompletedProcess(cmd, 0, "3\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(w, "gh", fake_gh)
    monkeypatch.setattr(w.subprocess, "run", fake_run)

    assert w.open_pr(7) is False
    out = capsys.readouterr().out
    assert "issue7: Push abgelehnt, keine FAILED-Zeile in der Push-Ausgabe." in out
    assert "remote: rejected" not in out
