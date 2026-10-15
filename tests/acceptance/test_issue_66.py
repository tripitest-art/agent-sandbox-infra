import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _lauf(extra_env):
    # PATH leer: weder bash noch python3 sind auffindbar; Python selbst wird absolut gestartet.
    env = {"PATH": "", "SystemRoot": os.environ.get("SystemRoot", ""), **extra_env}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_install_lib.py"],
        cwd=ROOT, env=env, capture_output=True, text=True)


def test_ohne_werkzeuge_wird_uebersprungen():
    r = _lauf({})
    # pytest meldet 5 („keine Tests gelaufen“), wenn die einzige Datei komplett übersprungen wird.
    assert r.returncode in (0, 5), r.stdout[-400:]
    assert "skipped" in r.stdout and "failed" not in r.stdout


def test_in_ci_ist_fehlendes_werkzeug_ein_fehler():
    r = _lauf({"CI": "true"})
    assert r.returncode != 0
