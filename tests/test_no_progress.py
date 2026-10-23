"""#281: Der Wächter-Thread in `run_agent()` beendet einen Agenten ohne Fortschritt.

Ein Lauf, der nur liest, belegte bisher die vollen `RUN_TIMEOUT` (30 Minuten über LiteLLM) und
damit Slot und Datei (Regel 8). Geprüft wird hier der Einbau in `run_agent()`: gefakter
`Popen`, gefaktes `time.sleep` und gefakte Uhr, damit kein Test wirklich 15 Minuten wartet.
"""
import importlib.util
import sys
import threading
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_no_progress_run", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_no_progress_run"] = w
_spec.loader.exec_module(w)

T0 = 1760880000.0
MIN = 60.0


class Uhr:
    """Setzbare Uhr: `time.sleep` schiebt sie weiter, statt zu warten."""

    def __init__(self):
        self.jetzt = T0

    def __call__(self) -> float:
        return self.jetzt


class FakeProc:
    """Agentenprozess ohne echten Start; `terminate()` beendet ihn."""

    def __init__(self):
        self.ende = threading.Event()
        self.beendet = 0
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.beendet += 1
        self.fertig(-15)

    def fertig(self, code: int) -> None:
        self.returncode = code
        self.ende.set()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Leitung:
    """stdout des Agenten: liefert keine Zeile und endet, sobald der Prozess fertig ist."""

    def __init__(self, proc: FakeProc):
        self.proc = proc

    def __iter__(self):
        assert self.proc.ende.wait(10), "Agent lief ohne Ende weiter"
        return iter(())


def _starte(monkeypatch, zustand, schluss_nach: int | None = None):
    """Baut `run_agent()` mit gefaktem Prozess, gefakter Uhr und gefaktem `time.sleep` auf."""
    uhr = Uhr()
    proc = FakeProc()
    aufrufe: list[str] = []
    monkeypatch.setattr(w, "WORKDIR", "/kein/workdir/281")
    monkeypatch.setattr(w, "jetzt_s", uhr)
    monkeypatch.setattr(w, "agent_identitaet", lambda: (None, None))
    monkeypatch.setattr(w, "RUN_TIMEOUT", 1800)
    monkeypatch.setattr(w.time, "sleep", lambda s: setattr(uhr, "jetzt", uhr.jetzt + s))
    monkeypatch.setattr(w.subprocess, "Popen", lambda cmd, **kw: proc)

    def stand(cwd: str):
        aufrufe.append(cwd)
        if schluss_nach is not None and len(aufrufe) > schluss_nach:
            proc.fertig(0)
        return zustand(len(aufrufe))

    monkeypatch.setattr(w, "workdir_zustand", stand)
    monkeypatch.setattr(proc, "stdout", Leitung(proc), raising=False)
    return proc, aufrufe


def test_run_agent_beendet_den_agenten_nach_15_minuten_ohne_aenderung(monkeypatch, capsys):
    proc, aufrufe = _starte(monkeypatch, zustand=lambda n: "unveraendert")

    ergebnis = w.run_agent("Auftrag")

    assert isinstance(ergebnis, int)
    assert ergebnis == -15
    assert proc.beendet == 1
    # Der Fingerabdruck wird vor dem Lauf und bei jeder Prüfung genommen, nicht nur einmal.
    assert len(aufrufe) >= 15
    zeilen = [z for z in capsys.readouterr().out.splitlines()
              if "WARNUNG: Agent hat seit 15 Minuten nichts geändert" in z]
    assert len(zeilen) == 1


def test_run_agent_laesst_einen_agenten_mit_aenderungen_laufen(monkeypatch, capsys):
    proc, aufrufe = _starte(monkeypatch, zustand=lambda n: f"stand-{n}", schluss_nach=20)

    ergebnis = w.run_agent("Auftrag")

    assert isinstance(ergebnis, int)
    assert ergebnis == 0                # der Agent beendet sich selbst
    assert proc.beendet == 0
    assert len(aufrufe) > 20            # mehr als 20 Prüfungen ohne Abbruch
    assert "WARNUNG" not in capsys.readouterr().out


def test_run_agent_beendet_nichts_bei_unbekanntem_zustand(monkeypatch, capsys):
    proc, aufrufe = _starte(monkeypatch, zustand=lambda n: None, schluss_nach=20)

    ergebnis = w.run_agent("Auftrag")

    assert isinstance(ergebnis, int)
    assert proc.beendet == 0
    assert len(aufrufe) > 20
    assert "WARNUNG" not in capsys.readouterr().out


def test_waechter_endet_mit_dem_prozess_ohne_zu_pruefen(monkeypatch):
    """Ist der Agent schon fertig, beendet der Thread den nächsten Prüfschritt nicht mehr."""
    proc, aufrufe = _starte(monkeypatch, zustand=lambda n: "gleich", schluss_nach=0)
    monkeypatch.setattr(w, "watchdog_pruefen",
                        lambda *a, **kw: pytest.fail("Wächter prüfte einen beendeten Prozess"))

    assert w.run_agent("Auftrag") == 0
    assert proc.beendet == 0
    assert len(aufrufe) == 1            # nur der Fingerabdruck vor dem Lauf
