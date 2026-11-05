import importlib.util
import os
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "core" / "benchmark_code.py"
_spec = importlib.util.spec_from_file_location("benchmark_code_gruppe", _PATH)
bc = importlib.util.module_from_spec(_spec)
sys.modules["benchmark_code_gruppe"] = bc
_spec.loader.exec_module(bc)


class Uhr:
    """Falsche Uhr: jeder Schlaf rückt die Zeit vor."""

    def __init__(self):
        self.t, self.schlaefe = 100.0, []

    def __call__(self):
        return self.t

    def schlafen(self, s):
        self.schlaefe.append(s)
        self.t += s


def _killpg(monkeypatch, antworten):
    """os.killpg fälschen: je Aufruf mit Signal 0 die nächste Antwort (None = lebt, sonst Ausnahme)."""
    aufrufe = []

    def killpg(pgid, sig):
        aufrufe.append((pgid, sig))
        antwort = antworten.pop(0) if antworten else None
        if antwort is not None:
            raise antwort

    monkeypatch.setattr(bc.os, "killpg", killpg)
    return aufrufe


def test_wartet_bis_die_gruppe_weg_ist(monkeypatch):
    aufrufe = _killpg(monkeypatch, [None, None, ProcessLookupError()])
    uhr = Uhr()
    assert bc.gruppe_abwarten(4242, frist_s=2.0, uhr=uhr, schlafen=uhr.schlafen) is True
    assert aufrufe == [(4242, 0)] * 3
    assert uhr.schlaefe == [0.02, 0.02]


def test_leere_gruppe_ohne_schlafen(monkeypatch):
    aufrufe = _killpg(monkeypatch, [ProcessLookupError()])
    uhr = Uhr()
    assert bc.gruppe_abwarten(7, uhr=uhr, schlafen=uhr.schlafen) is True
    assert aufrufe == [(7, 0)] and uhr.schlaefe == []


def test_frist_begrenzt_das_warten(monkeypatch):
    aufrufe = _killpg(monkeypatch, [])          # lebt immer
    uhr = Uhr()
    assert bc.gruppe_abwarten(7, frist_s=0.1, uhr=uhr, schlafen=uhr.schlafen) is False
    assert 4 <= len(aufrufe) <= 7
    assert round(uhr.t - 100.0, 6) >= 0.1


def test_keine_berechtigung_wirft_nie(monkeypatch):
    _killpg(monkeypatch, [PermissionError()])
    uhr = Uhr()
    assert bc.gruppe_abwarten(7, uhr=uhr, schlafen=uhr.schlafen) is False
    assert uhr.schlaefe == []


def test_zeitlimit_wartet_auf_die_gruppe(monkeypatch):
    gesehen = []
    monkeypatch.setattr(bc, "gruppe_abwarten", lambda pgid, *a, **k: gesehen.append(pgid) or True)
    ergebnis = bc.run_pruefung("import time\ntime.sleep(60)\n", "import loesung\n", timeout_s=1.0)
    assert ergebnis == (False, bc.GRUND_ZEITLIMIT)
    assert len(gesehen) == 1 and gesehen[0] > 0


def test_ohne_zeitlimit_kein_warten(monkeypatch):
    gesehen = []
    monkeypatch.setattr(bc, "gruppe_abwarten", lambda pgid, *a, **k: gesehen.append(pgid) or True)
    assert bc.run_pruefung("", "assert 1 + 1 == 2\n", timeout_s=10.0) == (True, "")
    assert gesehen == []


def test_enkel_ist_nach_dem_zeitlimit_weg(tmp_path):
    pid_datei = tmp_path / "kind.pid"
    code = (
        "import subprocess\n"
        "import sys\n"
        "kind = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_datei)!r}, 'w').write(str(kind.pid))\n"
        "import time\n"
        "time.sleep(120)\n"
    )
    assert bc.run_pruefung(code, "import loesung\n", timeout_s=3.0) == (False, bc.GRUND_ZEITLIMIT)
    assert pid_datei.exists(), "Prüfprozess hat die PID-Datei nicht geschrieben"
    pid = int(pid_datei.read_text(encoding="utf-8"))
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    assert stat.rsplit(")", 1)[1].split()[0] == "Z", f"Enkel {pid} lebt nach dem Zeitlimit noch"
