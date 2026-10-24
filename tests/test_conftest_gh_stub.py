"""Tests für den `gh`-Ersatz in tests/conftest.py (#321), ohne Netzwerk und ohne echtes `gh`."""

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

ERSATZ = Path(os.environ["SANDBOX_TEST_BIN"])
SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"


def test_ersatz_liegt_vorn_im_pfad_und_ist_ausfuehrbar():
    assert os.environ["PATH"].split(os.pathsep)[0] == str(ERSATZ)
    gh = ERSATZ / "gh"
    assert gh.is_file() and gh.stat().st_mode & stat.S_IXUSR
    assert gh.stat().st_mode & 0o777 == 0o755


def test_ersatz_endet_mit_exit_4_und_nur_einer_stderr_zeile():
    res = subprocess.run(["gh", "issue", "edit", "7", "--add-label", "in-arbeit"],
                         capture_output=True, text=True, env=os.environ)
    assert res.returncode == 4
    assert res.stdout == ""
    assert res.stderr.count("\n") == 1 and "GH_TOKEN" in res.stderr


def test_worker_gh_gibt_dafuer_wie_in_ci_none():
    spec = importlib.util.spec_from_file_location("worker_tick_gh_stub", SANDBOX / "worker_tick.py")
    w = importlib.util.module_from_spec(spec)
    sys.modules["worker_tick_gh_stub"] = w
    spec.loader.exec_module(w)
    assert w.gh("issue", "edit", "7", "--add-label", "in-arbeit") is None


def test_ersatz_verzeichnis_enthaelt_nur_den_ersatz():
    assert sorted(p.name for p in ERSATZ.iterdir()) in (["gh"], ["gh", "gh.cmd"])
