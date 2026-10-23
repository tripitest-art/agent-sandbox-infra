import importlib.util
import os
import sys
import tempfile
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _laden(name, datei):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / datei)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _unter_temp(pfad):
    temp = os.path.realpath(tempfile.gettempdir())
    return os.path.realpath(pfad).startswith(temp + os.sep)


def test_umgebung_zeigt_auf_temp_verzeichnisse():
    for name in ("SLOT_DIR", "AUDIT_LOG", "STATE", "REVIEW_STATE", "REVIEW_DRAFTS"):
        pfad = os.environ.get(name, "")
        assert pfad, name
        assert _unter_temp(pfad), name
    assert os.path.isdir(os.environ["SLOT_DIR"])


def test_worker_tick_schreibt_nicht_in_die_echten_dateien():
    w = _laden("worker_tick_isoliert", "worker_tick.py")
    for pfad in (w.SLOT_DIR, w.AUDIT_LOG, w.STATE):
        assert _unter_temp(pfad), pfad


def test_review_tick_schreibt_nicht_in_die_echten_dateien():
    r = _laden("review_tick_isoliert", "review_tick.py")
    for pfad in (r.AUDIT_LOG, r.BASE_STATE, r.BASE_DRAFT_DIR):
        assert _unter_temp(pfad), pfad


def test_dashboard_liest_nicht_die_echten_slots():
    d = _laden("dashboard_isoliert", "dashboard.py")
    assert _unter_temp(d.SLOT_DIR)
