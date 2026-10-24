import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Der Takt läuft im CI-Job tests-als-root als root, ohne Nutzer `worker`: os.geteuid() == 0 simulieren.
ALS_ROOT = ("import os, sys, pytest; os.geteuid = lambda: 0; "
            "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider'] + sys.argv[1:]))")


@pytest.mark.parametrize("name", ["test_main_accepts_text_only_rework", "test_main_reports_new_commit"])
@pytest.mark.parametrize("als_root", [False, True])
def test_nacharbeit_test_gruen_einzeln_und_als_root(name, als_root):
    ziel = f"tests/test_worker_tick.py::{name}"
    cmd = [sys.executable, "-c", ALS_ROOT, ziel] if als_root else [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", ziel]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout[-2000:]
