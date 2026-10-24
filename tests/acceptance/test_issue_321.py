import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _laden(name, datei):
    spec = importlib.util.spec_from_file_location(name, ROOT / "sandbox" / datei)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _im_ersatz(pfad):
    ersatz = os.environ.get("SANDBOX_TEST_BIN")
    assert ersatz, "conftest.py setzt SANDBOX_TEST_BIN nicht"
    assert pfad is not None, "kein gh auf dem PATH gefunden"
    return Path(pfad).resolve().parent == Path(ersatz).resolve()


def test_gh_auf_dem_test_pfad_ist_der_ersatz():
    assert _im_ersatz(shutil.which("gh"))


def test_takt_skripte_finden_den_ersatz_zuerst():
    w = _laden("worker_tick_gh_ersatz", "worker_tick.py")
    r = _laden("review_tick_gh_ersatz", "review_tick.py")
    assert _im_ersatz(shutil.which("gh", path=w.ENV["PATH"]))
    assert _im_ersatz(shutil.which("gh", path=r.ENV["PATH"]))
    assert _im_ersatz(shutil.which("gh", path=w.main_health_env()["PATH"]))


def test_ersatz_verhaelt_sich_wie_gh_ohne_anmeldung():
    w = _laden("worker_tick_gh_ersatz2", "worker_tick.py")
    res = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, env=w.ENV)
    assert res.returncode == 4
    assert "GH_TOKEN" in res.stderr
    assert res.stdout == ""
