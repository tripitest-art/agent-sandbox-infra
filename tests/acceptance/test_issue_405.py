import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_unreleased", _PATH)
w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w)

BODY_A = (
    "### Betroffene Dateien (nur diese)\n\n"
    "- `sc_digger/web/app.py`\n"
    "- `CHANGELOG.md` — eine Zeile unter `Unreleased` mit der PR-Nummer\n"
)
BODY_B = (
    "### Betroffene Dateien (nur diese)\n\n"
    "- `_eval/` — komplett löschen\n"
    "- `CHANGELOG.md` — eine Zeile unter `Unreleased` (Abschnitt `### Entfernt`)\n"
)


def test_unreleased_loest_keine_kollision_aus():
    a = w.collision_files(w.parse_affected_files(BODY_A))
    b = w.collision_files(w.parse_affected_files(BODY_B))
    assert a == {"sc_digger/web/app.py"}
    assert b == {"_eval"}
    assert not (a & b)


def test_echte_dateien_kollidieren_weiter():
    a = w.collision_files({"Unreleased", "sandbox/worker_tick.py", "Dockerfile"})
    assert a == {"sandbox/worker_tick.py", "Dockerfile"}
