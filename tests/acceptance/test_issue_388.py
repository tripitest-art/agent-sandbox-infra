import importlib.util
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
_PFAD = ROOT / "sandbox" / "benchmark.py"

def _laden():
    spec = importlib.util.spec_from_file_location("benchmark_runner", _PFAD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

def test_wenn_frei_ueberspringt_bei_aktivem_lock(tmp_path, monkeypatch, capsys):
    bm = _laden()
    lock_file = tmp_path / "qwen-worker-w1.lock"
    lock_file.write_text("12345\n")

    monkeypatch.setattr(bm, "LOCK_PFADE", [str(tmp_path / "qwen-worker-w*.lock")])
    
    # Aufruf mit --wenn-frei muss True bei Belegung liefern
    belegt = bm.ist_slot_belegt([str(tmp_path / "qwen-worker-w*.lock")])
    assert belegt is True

def test_wenn_frei_laeuft_wenn_kein_lock(tmp_path, monkeypatch):
    bm = _laden()
    belegt = bm.ist_slot_belegt([str(tmp_path / "qwen-worker-w*.lock")])
    assert belegt is False

def test_systemd_units_existieren_und_sind_valide():
    service_file = ROOT / "sandbox" / "systemd" / "agent-benchmark.service"
    timer_file = ROOT / "sandbox" / "systemd" / "agent-benchmark.timer"

    assert service_file.exists(), "agent-benchmark.service fehlt"
    assert timer_file.exists(), "agent-benchmark.timer fehlt"

    service_content = service_file.read_text(encoding="utf-8")
    timer_content = timer_file.read_text(encoding="utf-8")

    assert "--wenn-frei" in service_content
    assert "OnCalendar=" in timer_content
    assert "WantedBy=timers.target" in timer_content
