"""`tests/conftest.py` räumt seine Temp-Verzeichnisse beim Prozessende auf (#453).

Ohne Aufräumen blieben je pytest-Lauf `sandbox-tests-*` und `sandbox-run-*` in /tmp liegen; auf dem Server
füllte das die Platte. Der Test lädt conftest.py in einem Unterprozess, weil `atexit` erst beim Ende
des Prozesses greift.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

CONFTEST = Path(__file__).resolve().parent / "conftest.py"

# Gibt die Verzeichnisse aus, die conftest.py angelegt hat, und ob sie jetzt (noch) existieren.
_PROBE = """
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("conftest_probe", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
pfade = {
    "projekte": os.path.dirname(os.environ["AGENT_PROJECTS"]),
    "lauf": os.path.dirname(os.environ["SLOT_DIR"]),
    "bin": os.environ["SANDBOX_TEST_BIN"],
}
print(json.dumps({"pfade": pfade, "da": {k: os.path.isdir(v) for k, v in pfade.items()}}))
"""


def test_verzeichnisse_existieren_im_lauf_und_sind_danach_weg(tmp_path):
    env = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    res = subprocess.run([sys.executable, "-c", _PROBE, str(CONFTEST)], env=env,
                         capture_output=True, text=True, check=False, timeout=60)
    assert res.returncode == 0, res.stderr
    info = json.loads(res.stdout.strip().splitlines()[-1])
    assert info["da"] == {"projekte": True, "lauf": True, "bin": True}
    for name, pfad in info["pfade"].items():
        assert Path(pfad).parent == tmp_path, name      # wirklich unter dem umgelenkten TMPDIR angelegt
        assert not os.path.exists(pfad), name           # nach Prozessende entfernt
    assert list(tmp_path.iterdir()) == []
