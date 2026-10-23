"""Akzeptanztests für Issue #227: Kontext-Komprimierung via Repomap.

Der Taktgeber hängt dem Agenten-Auftrag eine kompakte Symbol-Map der in
„Betroffene Dateien" genannten Python-Dateien (plus 1-Hop-Abhängigkeiten) an.
Ohne Netzwerk, ohne gh, ohne Server; Module laden per Pfad wie in
tests/test_worker_tick.py und tests/acceptance/test_issue_296.py.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_PATH_WT = ROOT / "sandbox" / "worker_tick.py"
_PATH_RM = ROOT / "sandbox" / "repomap.py"

_spec_wt = importlib.util.spec_from_file_location("worker_tick_227", _PATH_WT)
w = importlib.util.module_from_spec(_spec_wt)
sys.modules["worker_tick_227"] = w
_spec_wt.loader.exec_module(w)

_spec_rm = importlib.util.spec_from_file_location("repomap_227", _PATH_RM)
if not _PATH_RM.exists():
    raise ImportError("sandbox/repomap.py fehlt — Umsetzung aus Issue #227 offen")
repomap = importlib.util.module_from_spec(_spec_rm)
sys.modules["repomap_227"] = repomap
_spec_rm.loader.exec_module(repomap)

REPOMAP_KOPF = "----- Repomap (vom Taktgeber erzeugt) -----"
REPOMAP_ENDE = "----- Ende Repomap -----"


class FakeGH:
    """gh-Ersatz nach dem Muster von test_issue_296.py: Kandidaten, `in-arbeit`-Liste
    und `blockiert`-Liste getrennt; der Kandidat hat Autor und eine Sektion
    „Betroffene Dateien" (Regel 8)."""

    def __init__(self, issues, active=()):
        self.issues = list(issues)
        self.active = list(active)

    def __call__(self, *args, **kwargs):
        if args[:2] == ("issue", "list"):
            if "in-arbeit" in args:
                return self.active
            if "blockiert" in args:
                return []
            return self.issues
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Test Issue"}
        if args[:2] == ("issue", "edit"):
            return None
        if args[:2] in (("pr", "list"), ("run", "list")) or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _kandidat(nummer=227, body=None):
    return {
        "number": nummer,
        "title": "Test Issue",
        "body": body if body is not None else
        "### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `sandbox/a.py`\n",
        "author": {"login": w.OWNER},
        "labels": [{"name": "bereit"}],
    }


def test_repomap_liest_signaturen_einer_py_datei(tmp_path):
    """Funktions- und Klassen-Signaturen landen im Repomap-Text, auch Klassenmethoden."""
    (tmp_path / "foo.py").write_text(
        "import json\n\n\ndef hello(name: str) -> str:\n"
        "    return f'Hi {name}'\n\n\nclass Greeter:\n"
        "    def greet(self) -> None:\n        print(hello('World'))\n",
        encoding="utf-8",
    )
    karte = repomap.build_repomap(tmp_path, ["foo.py"])
    assert "foo.py" in karte
    assert "def hello" in karte
    assert "class Greeter" in karte
    assert "def greet" in karte
    assert "import json" in karte


def test_repomap_nimmt_1_hop_importe_mit(tmp_path):
    """Import-Ziele, die auf .py-Dateien im Repo auflösen, werden mit einer Ebene gelesen."""
    (tmp_path / "helpers.py").write_text("def helper_funktion() -> int:\n    return 1\n",
                                         encoding="utf-8")
    (tmp_path / "foo.py").write_text("import helpers\n\n\ndef nutzer():\n"
                                     "    return helpers.helper_funktion()\n",
                                     encoding="utf-8")
    karte = repomap.build_repomap(tmp_path, ["foo.py"])
    assert "def helper_funktion" in karte


def test_repomap_begrenzt_ausgabe_laenge(tmp_path):
    """Bei vielen Symbolen hält eine kleine Grenze den Text kurz."""
    viele = "\n".join(f"def abfunction_{i}():\n    pass\n" for i in range(200))
    (tmp_path / "gross.py").write_text(viele, encoding="utf-8")
    karte = repomap.build_repomap(tmp_path, ["gross.py"], max_tokens=64)
    assert len(karte) <= 64 * 4
    assert "def abfunction_0" in karte  # Kürzen geschieht am Ende, nicht am Anfang


def test_repomap_ignoriert_fehlende_und_nicht_py_dateien(tmp_path):
    """Nicht vorhandene Pfade und Nicht-Python-Dateien werfen keinen Fehler."""
    (tmp_path / "vorhanden.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM x\n", encoding="utf-8")
    karte = repomap.build_repomap(tmp_path, ["vorhanden.py", "fehlt.py", "Dockerfile"])
    assert "def foo" in karte
    assert "fehlt.py" not in karte
    assert "Dockerfile" not in karte


def test_repomap_ist_deterministisch_und_dedupliziert(tmp_path):
    """Doppelte, unsortierte Eingabe: gleiche Ausgabe wie die sortierte, kein Doppelkapitel."""
    (tmp_path / "b_.py").write_text("def zzz():\n    pass\n", encoding="utf-8")
    (tmp_path / "a_.py").write_text("def aaa():\n    pass\n", encoding="utf-8")
    karte1 = repomap.build_repomap(tmp_path, ["b_.py", "a_.py", "b_.py", "a_.py"])
    karte2 = repomap.build_repomap(tmp_path, ["a_.py", "b_.py"])
    assert karte1 == karte2


def test_repomap_leer_ohne_gueltige_dateien(tmp_path):
    """Ohne Treffer kommt ein leerer Text; daran erkennt der Taktgeber „nichts anzuhängen"."""
    assert repomap.build_repomap(tmp_path, ["fehlt.py"]) == ""


def test_issue_job_haengt_repomap_nur_bei_treffer_an(monkeypatch, tmp_path):
    """Ohne Treffer bleibt der Auftrag wie bisher — bestehende Auftrags-Tests bleiben grün."""
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(w, "gh", FakeGH([_kandidat()]))
    ergebnis = w.issue_job()
    assert ergebnis is not None, "issue_job() fand den Kandidaten nicht"
    key, task, pr_no, sha = ergebnis
    assert key == "issue227"
    assert REPOMAP_KOPF not in task
    assert REPOMAP_ENDE not in task
    # Der Auftrag selbst bleibt inhaltlich erhalten (Issue-Text wörtlich).
    assert "### Betroffene Dateien" in task


def test_issue_job_baut_repomap_aus_betroffenen_dateien(monkeypatch, tmp_path):
    """Der Auftrag enthält die echten Signaturen der betroffenen Datei und die Marker."""
    (tmp_path / "mini_modul.py").write_text("def mini_zielfunktion():\n    return 1\n",
                                            encoding="utf-8")
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    body = "### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `mini_modul.py`\n"
    monkeypatch.setattr(w, "gh", FakeGH([_kandidat(body=body)]))
    key, task, pr_no, sha = w.issue_job()
    assert key == "issue227"
    assert REPOMAP_KOPF in task
    assert REPOMAP_ENDE in task
    assert "mini_modul.py" in task
    assert "def mini_zielfunktion" in task
    # Der Issue-Text bleibt Bestandteil des Auftrags.
    assert "### Ziel" in task
