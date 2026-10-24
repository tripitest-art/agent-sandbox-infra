import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["test_main_accepts_text_only_rework", "test_main_reports_new_commit"])
def test_nacharbeit_test_laeuft_allein_gruen(name):
    # Allein ausgeführt ist der Zwischenspeicher der PR-Vorlage leer; vorher: NoneType-Fehler in pr_template().
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         f"tests/test_worker_tick.py::{name}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout[-2000:]
