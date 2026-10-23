import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_slotschutz", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_slotschutz"] = w
_spec.loader.exec_module(w)

FREMD = 424242


def _datei(tmp_path):
    return tmp_path / "slot-1.json"


def _vorbereiten(tmp_path, **daten):
    _datei(tmp_path).write_text(json.dumps({"slot": "1", **daten}), encoding="utf-8")


def _lesen(tmp_path):
    return json.loads(_datei(tmp_path).read_text(encoding="utf-8"))


def _lebt(*lebende):
    return lambda pid: pid in lebende


def test_idle_ueberschreibt_keinen_laufenden_fremden_slot(tmp_path, capsys):
    _vorbereiten(tmp_path, status="running", pid=FREMD, issue=281)
    w.write_slot_status("idle", slot="1", message="Takt beendet", project="sc-digger",
                        slot_dir=str(tmp_path), lebt=_lebt(FREMD))
    assert _lesen(tmp_path)["status"] == "running"
    assert _lesen(tmp_path)["issue"] == 281
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "übergangen" in z]
    assert len(zeilen) == 1
    assert "Slot 1" in zeilen[0] and str(FREMD) in zeilen[0]


def test_running_ueberschreibt_immer(tmp_path):
    _vorbereiten(tmp_path, status="running", pid=FREMD, issue=281)
    w.write_slot_status("running", slot="1", issue=282, pid=w.os.getpid(),
                        slot_dir=str(tmp_path), lebt=_lebt(FREMD))
    assert _lesen(tmp_path)["issue"] == 282


def test_idle_ueberschreibt_wenn_die_fremde_pid_tot_ist(tmp_path):
    _vorbereiten(tmp_path, status="running", pid=FREMD)
    w.write_slot_status("idle", slot="1", message="Takt beendet", slot_dir=str(tmp_path),
                        lebt=_lebt())
    assert _lesen(tmp_path)["status"] == "idle"


def test_idle_ueberschreibt_den_eigenen_laufenden_slot(tmp_path):
    _vorbereiten(tmp_path, status="running", pid=w.os.getpid())
    w.write_slot_status("idle", slot="1", message="Takt beendet", slot_dir=str(tmp_path),
                        lebt=_lebt(w.os.getpid()))
    assert _lesen(tmp_path)["status"] == "idle"


def test_idle_schreibt_in_fehlende_oder_kaputte_datei(tmp_path):
    w.write_slot_status("idle", slot="1", slot_dir=str(tmp_path), lebt=_lebt(FREMD))
    assert _lesen(tmp_path)["status"] == "idle"
    _datei(tmp_path).write_text("{kaputt", encoding="utf-8")
    w.write_slot_status("idle", slot="1", slot_dir=str(tmp_path), lebt=_lebt(FREMD))
    assert _lesen(tmp_path)["status"] == "idle"


def test_idle_ueberschreibt_bei_idle_oder_ohne_pid(tmp_path):
    _vorbereiten(tmp_path, status="idle", pid=FREMD)
    w.write_slot_status("idle", slot="1", message="neu", slot_dir=str(tmp_path), lebt=_lebt(FREMD))
    assert _lesen(tmp_path)["message"] == "neu"
    _vorbereiten(tmp_path, status="running")
    w.write_slot_status("idle", slot="1", message="neu2", slot_dir=str(tmp_path), lebt=_lebt(FREMD))
    assert _lesen(tmp_path)["message"] == "neu2"


def test_slot_belegt_von_anderem_lauf_liefert_die_fremde_pid(tmp_path):
    assert w.slot_belegt_von_anderem_lauf(str(tmp_path), "1", 1, _lebt(FREMD)) is None
    _vorbereiten(tmp_path, status="running", pid=FREMD)
    assert w.slot_belegt_von_anderem_lauf(str(tmp_path), "1", 1, _lebt(FREMD)) == FREMD
    assert w.slot_belegt_von_anderem_lauf(str(tmp_path), "1", FREMD, _lebt(FREMD)) is None
    assert w.slot_belegt_von_anderem_lauf(str(tmp_path), "1", 1, _lebt()) is None
    _vorbereiten(tmp_path, status="running", pid="abc")
    assert w.slot_belegt_von_anderem_lauf(str(tmp_path), "1", 1, _lebt(FREMD)) is None
    _datei(tmp_path).write_text("[1, 2]", encoding="utf-8")
    assert w.slot_belegt_von_anderem_lauf(str(tmp_path), "1", 1, _lebt(FREMD)) is None


def test_pid_lebt_zaehlt_permissionerror_als_lebendig_und_alles_andere_als_tot(monkeypatch):
    def kill_fremd(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(w.os, "kill", kill_fremd)
    assert w.pid_lebt(FREMD) is True

    def kill_weg(pid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(w.os, "kill", kill_weg)
    assert w.pid_lebt(FREMD) is False
    monkeypatch.setattr(w.os, "kill", lambda pid, sig: None)
    assert w.pid_lebt(FREMD) is True
