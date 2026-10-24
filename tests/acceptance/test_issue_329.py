import importlib.util
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_push_diagnose", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_push_diagnose"] = w
_spec.loader.exec_module(w)


def test_helfer_nennt_nur_die_node_ids():
    ausgabe = (
        "pre-push: Tests fehlgeschlagen, Push abgelehnt.\n"
        "FAILED tests/test_a.py::test_b - AssertionError: token ghp_GEHEIM123\n"
        "FAILED tests/test_c.py::test_d\n"
    )
    assert w.rote_tests_aus_push_ausgabe(ausgabe) == [
        "tests/test_a.py::test_b",
        "tests/test_c.py::test_d",
    ]


def test_helfer_begrenzt_auf_drei_und_entdoppelt():
    ausgabe = "".join(f"FAILED tests/t{i}.py::test_{i}\n" for i in range(5))
    ausgabe += "FAILED tests/t0.py::test_0\n"
    assert w.rote_tests_aus_push_ausgabe(ausgabe) == [
        "tests/t0.py::test_0",
        "tests/t1.py::test_1",
        "tests/t2.py::test_2",
    ]
    assert w.rote_tests_aus_push_ausgabe(ausgabe, limit=0) == []


def test_helfer_ohne_failed_zeile_ist_leer():
    assert w.rote_tests_aus_push_ausgabe("pre-push: Tests fehlgeschlagen\nremote: rejected\n") == []


def test_open_pr_loggt_rote_tests_ohne_details(monkeypatch, tmp_path, capsys):
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(w, "WORKDIR", str(wt))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", "## Vorlage\n")
    monkeypatch.setattr(w, "alarm", lambda *a, **k: None)

    def fake_gh(*args):
        if args[:2] == ("pr", "list"):
            return []
        if args[:2] == ("issue", "view"):
            return {"title": "Titel des Issues"}
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    def fake_run(cmd, **kw):
        if list(cmd[:2]) == ["git", "push"]:
            return subprocess.CompletedProcess(
                cmd, 1,
                "pre-push: Tests fehlgeschlagen, Push abgelehnt.\n"
                "FAILED tests/test_a.py::test_b - AssertionError: ghp_GEHEIM123\n",
                "",
            )
        if list(cmd[:3]) == ["git", "rev-list", "--count"]:
            return subprocess.CompletedProcess(cmd, 0, "3\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(w, "gh", fake_gh)
    monkeypatch.setattr(w.subprocess, "run", fake_run)

    assert w.open_pr(7) is False
    out = capsys.readouterr().out
    assert "tests/test_a.py::test_b" in out
    assert "ghp_GEHEIM123" not in out and "AssertionError" not in out
