"""Schutz der Slot-Datei vor überschreibenden Schreibern (#288), ohne echte Slot-Datei.

Der Akzeptanztest deckt `write_slot_status()` ab; hier geht es um den Wrapper `slot_status()`,
der die Telemetrie nie zum Anhalten des Takts machen darf (Regel 7).
"""

import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_slot_schutz", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_slot_schutz"] = w
_spec.loader.exec_module(w)

FREMD = 424242


def _schreibe_slot(slot_dir: Path, **daten) -> None:
    (slot_dir / "slot-1.json").write_text(json.dumps({"slot": "1", **daten}), encoding="utf-8")


def _lese_slot(slot_dir: Path) -> dict:
    return json.loads((slot_dir / "slot-1.json").read_text(encoding="utf-8"))


def test_slot_status_ueber_laufenden_fremden_slot_wirft_nicht(monkeypatch, capsys, tmp_path):
    # Fertig-Kriterium aus #288: der Schutz ändert nichts daran, dass slot_status nie wirft.
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path))
    _schreibe_slot(tmp_path, status="running", pid=FREMD, issue=281)
    w.slot_status("idle", slot="1", message="Takt beendet", lebt=lambda pid: pid == FREMD)
    daten = _lese_slot(tmp_path)
    assert daten["status"] == "running" and daten["issue"] == 281
    assert "übergangen" in capsys.readouterr().out


def test_slot_status_schreibt_trotz_laufendem_eigenem_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path))
    _schreibe_slot(tmp_path, status="running", pid=w.os.getpid())
    w.slot_status("idle", slot="1", message="fertig", lebt=lambda pid: True)
    assert _lese_slot(tmp_path)["message"] == "fertig"


def test_slot_status_schreibt_ueber_kaputte_datei(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path))
    (tmp_path / "slot-1.json").write_text("{kaputt", encoding="utf-8")
    w.slot_status("idle", slot="1", message="neu")
    assert _lese_slot(tmp_path)["message"] == "neu"
