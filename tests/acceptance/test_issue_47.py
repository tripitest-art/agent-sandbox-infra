"""Akzeptanztests für Issue #<N>: `__init__.py` im Testbaum ist geschützte Infrastruktur.

Warum: Der Code einer `__init__.py` läuft beim Einsammeln, bevor ein eingefrorener
Akzeptanztest importiert wird. Er kann also das Modul unter Test ersetzen. `tests/acceptance/`
fängt Regel 1 nur für **bestehende** Dateien ab; eine neu hinzugefügte `__init__.py` und
`tests/__init__.py` erfassen die Regeln 1 und 4 heute nicht.
"""
import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "acceptance_guard", Path(__file__).resolve().parents[2] / ".github" / "scripts" / "acceptance_guard.py")
g = importlib.util.module_from_spec(_SPEC)
sys.modules["acceptance_guard"] = g
_SPEC.loader.exec_module(g)


def _file(name, status="modified", patch=""):
    return {"filename": name, "status": status, "patch": patch}


def _reader(files):
    return lambda p: files.get(p)


def test_init_py_in_tests_ist_geschuetzte_infrastruktur():
    for pfad in ("tests/__init__.py", "tests/acceptance/__init__.py", "tests/unit/__init__.py"):
        res = g.evaluate([_file(pfad, "added", patch="+import sc_digger")], set(), {}, _reader({}))
        assert not res.ok, f"{pfad} wird nicht als Infrastruktur erkannt"


def test_init_py_in_tests_kann_freigegeben_werden():
    res = g.evaluate([_file("tests/__init__.py", "added", patch="+x = 1")],
                     {g.OVERRIDE_LABEL}, {}, _reader({}))
    assert res.ok and any("Erlaubt" in n for n in res.notes)


def test_init_py_ausserhalb_des_testbaums_ist_normaler_code():
    res = g.evaluate([_file("sc_digger/__init__.py", "added", patch="+x = 1")], set(), {}, _reader({}))
    assert res.ok
