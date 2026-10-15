"""Akzeptanztests für Issue #<N>: Regel 8 darf nicht offen ausfallen (worker_tick)."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("worker_tick", ROOT / "sandbox" / "worker_tick.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

ISSUE = {
    "number": 7,
    "title": "Titel",
    "labels": [{"name": "bereit"}],
    "author": {"login": "tripitest-art"},
    "body": "### Betroffene Dateien\n\n- `sandbox/worker_tick.py`\n",
}


class FakeGH:
    """gh: liefert die Kandidaten, kann aber die Abfrage der aktiven Aufträge scheitern lassen."""

    def __init__(self, issues, fail_active=False):
        self.issues, self.fail_active, self.calls = list(issues), fail_active, []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("issue", "list") and "in-arbeit" in args:
            if self.fail_active:
                raise RuntimeError("gh nicht erreichbar")
            return []
        if args[:2] == ("issue", "list"):
            return self.issues
        return None                     # issue edit u. Ä. geben die URL aus


def test_datei_ohne_endung_zaehlt():
    assert w.parse_affected_files("### Betroffene Dateien\n\n- `Dockerfile`\n") == {"Dockerfile"}


def test_verzeichnis_zaehlt():
    assert w.parse_affected_files("### Betroffene Dateien\n\n- `sandbox/`\n") == {"sandbox"}


def test_ueberschrift_mit_zusatz_wird_erkannt():
    body = "### Betroffene Dateien (nur diese)\n\n- `a/b.py`\n"
    assert w.parse_affected_files(body) == {"a/b.py"}


def test_fehlende_sektion_ist_unbekannt():
    assert w.parse_affected_files("### Ziel\n\nEtwas tun.\n") is None


def test_abfrage_der_aktiven_auftraege_scheitert_laut(monkeypatch):
    monkeypatch.setattr(w, "gh", FakeGH([], fail_active=True))
    assert w.active_in_progress_issues() is None


def test_issue_job_blockiert_bei_unbekannter_lage(monkeypatch):
    # Fail-open: eine gescheiterte Abfrage darf nicht als „keine Kollision“ gelten.
    monkeypatch.setattr(w, "gh", FakeGH([ISSUE], fail_active=True))
    assert w.issue_job() is None


def test_issue_job_ueberspringt_issue_ohne_dateiliste(monkeypatch):
    ohne = dict(ISSUE, number=8, body="### Ziel\n\nOhne Dateiliste.\n")
    monkeypatch.setattr(w, "gh", FakeGH([ohne, ISSUE]))
    key, _, _, _ = w.issue_job()
    assert key == "issue7"


def test_beispielprojektliste_hat_hotspot():
    data = json.loads((ROOT / "sandbox" / "projects.example.json").read_text(encoding="utf-8"))
    infra = next(p for p in data["projects"] if p["name"] == "agent-sandbox-infra")
    assert infra.get("hotspot") == "sandbox/worker_tick.py"
    assert infra.get("hotspot_label")
