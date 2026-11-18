import os
import subprocess
import sys
from pathlib import Path

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"

_PROBE = """
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("conftest_probe", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(json.dumps({k: os.path.isdir(os.path.dirname(os.environ[k]) if k != "SLOT_DIR" else os.environ[k])
                  for k in ("AGENT_PROJECTS", "SLOT_DIR", "SANDBOX_TEST_BIN")}))
"""


def _lauf(tmp_path):
    env = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    return subprocess.run([sys.executable, "-c", _PROBE, str(CONFTEST)], env=env,
                          capture_output=True, text=True, check=False, timeout=60)


def test_conftest_raeumt_seine_tempverzeichnisse_beim_prozessende_auf(tmp_path):
    res = _lauf(tmp_path)
    assert res.returncode == 0, res.stderr
    uebrig = sorted(p.name for p in tmp_path.iterdir()
                    if p.name.startswith(("sandbox-tests-", "sandbox-run-", "sandbox-bin-")))
    assert uebrig == []


def test_verzeichnisse_existieren_waehrend_des_prozesses(tmp_path):
    res = _lauf(tmp_path)
    assert res.returncode == 0, res.stderr
    zeilen = res.stdout.strip().splitlines()
    assert zeilen[-1] == '{"AGENT_PROJECTS": true, "SLOT_DIR": true, "SANDBOX_TEST_BIN": true}'
