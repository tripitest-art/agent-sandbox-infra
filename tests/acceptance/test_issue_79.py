import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_issue_regel8", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_issue_regel8"] = w
_spec.loader.exec_module(w)


def _body(sektion):
    return f"### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n{sektion}\n### Schnittstellen\n\nx\n"


def test_keine_andere_datei_absatz_zaehlt_nicht():
    body = _body("- `sandbox/a.py`\n- `tests/test_a.py`\n\n"
                 "Keine andere Datei ändert sich. `install.sh`, `review_tick.py` und `projects.py` bleiben unberührt.\n")
    assert w.parse_affected_files(body) == {"sandbox/a.py", "tests/test_a.py"}


def test_ausschluss_ueber_mehrere_zeilen_eines_absatzes():
    body = _body("- `sandbox/a.py`\n\n"
                 "Keine andere Datei ändert sich. `sandbox/ha_status.py` bleibt unberührt, ebenso `sandbox/systemd/`\n"
                 "(die Units entstehen per Heredoc in `install.sh`).\n")
    assert w.parse_affected_files(body) == {"sandbox/a.py"}


def test_aufzaehlungszeile_mit_ausschlusswort_zaehlt_nicht():
    body = _body("- `sandbox/a.py`\n- `sandbox/b.py` bleibt unberührt\n- `tests/test_a.py`\n")
    assert w.parse_affected_files(body) == {"sandbox/a.py", "tests/test_a.py"}


def test_prosa_ohne_ausschlusswort_zaehlt_wie_bisher():
    body = _body("- `sandbox/a.py`\n\nZusätzlich wird `sandbox/b.py` geändert.\n")
    assert w.parse_affected_files(body) == {"sandbox/a.py", "sandbox/b.py"}


def test_nur_ausschlusssatz_ergibt_leere_menge_nicht_none():
    body = _body("Keine Datei wird angefasst, `install.sh` bleibt unberührt.\n")
    assert w.parse_affected_files(body) == set()
    assert w.parse_affected_files("### Ziel\n\nOhne Sektion.\n") is None
