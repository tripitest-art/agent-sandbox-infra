"""Akzeptanztests für Issue #59: Ein Nacharbeits-Auftrag läuft nur in einem Slot und wird nicht wiederholt.

Die Beispielschlüssel sind bewusst zusammengesetzt statt wörtlich zitiert; die Tests simulieren
den zweiten Slot mit einem fremden, lebenden Prozess und den ersten mit einem beendeten.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "worker_tick", Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py")
w = importlib.util.module_from_spec(_SPEC)
sys.modules["worker_tick"] = w
_SPEC.loader.exec_module(w)

KEY = "pr" + "287" + "@2026-10-15T10:35:45Z"


@pytest.fixture(autouse=True)
def _claims(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"), raising=False)
    monkeypatch.setattr(w, "PREFIX", "")


@pytest.fixture
def other_slot():
    """Ein lebender fremder Prozess, der den Slot eines anderen Taktgebers vertritt."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield proc.pid
    proc.kill()
    proc.wait()


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _claim_by(pid: int) -> None:
    path = Path(w.claim_path(KEY))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def _fake_jobs(monkeypatch):
    """Nur die Nacharbeit liefert einen Auftrag; er respektiert `done` und `skip` wie der echte."""
    def rework(done=frozenset(), skip=None):
        if KEY in done or (skip and skip(KEY)):
            return None
        return (KEY, "Aufgabe", 287, None)
    monkeypatch.setattr(w, "rework_job", rework)
    monkeypatch.setattr(w, "text_fix_job", lambda skip=None: None)
    monkeypatch.setattr(w, "issue_job", lambda skip=None: None)


def test_eigener_claim_wird_geschrieben_und_freigegeben():
    assert w.claim_job(KEY) is True
    assert Path(w.claim_path(KEY)).read_text(encoding="utf-8").strip() == str(os.getpid())
    w.release_job(KEY)
    assert not Path(w.claim_path(KEY)).exists()
    assert w.claim_job(KEY) is True


def test_claim_eines_anderen_lebenden_slots_wird_nicht_uebernommen(other_slot):
    _claim_by(other_slot)
    assert w.claim_job(KEY) is False
    assert Path(w.claim_path(KEY)).read_text(encoding="utf-8").strip() == str(other_slot)


def test_claim_eines_beendeten_prozesses_wird_uebernommen():
    _claim_by(_dead_pid())
    assert w.claim_job(KEY) is True
    assert Path(w.claim_path(KEY)).read_text(encoding="utf-8").strip() == str(os.getpid())


def test_find_job_ueberspringt_den_belegten_auftrag(monkeypatch, other_slot):
    _fake_jobs(monkeypatch)
    assert w.find_job({})[0] == KEY            # ohne Claim: der Auftrag kommt dran
    _claim_by(other_slot)
    assert w.find_job({}) is None              # belegt: der Slot nimmt etwas anderes oder nichts


def test_erledigte_nacharbeit_gilt_fuer_alle_slots(monkeypatch):
    _fake_jobs(monkeypatch)
    w.mark_job_done(KEY)
    assert w.find_job({}) is None              # leerer Zustand = der andere Slot kennt den Auftrag nicht
