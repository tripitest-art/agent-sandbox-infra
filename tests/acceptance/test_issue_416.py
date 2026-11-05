import importlib.util
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load(name, datei):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "sandbox" / datei)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load("worker_tick_deploy_marke", "worker_tick.py")
ad = _load("autodeploy_deploy_marke", "autodeploy.py")


def test_conftest_biegt_die_deploy_marke_um():
    pfad = os.environ.get("DEPLOY_PENDING_FILE")
    assert pfad, "DEPLOY_PENDING_FILE ist in den Tests nicht gesetzt"
    assert pfad != "/run/agent-deploy-pending"
    assert not os.path.exists(pfad)


def test_takt_liest_nie_die_marke_des_servers():
    assert w.DEPLOY_PENDING_FILE == os.environ.get("DEPLOY_PENDING_FILE")
    assert w.deploy_steht_an({}) is False


def test_autodeploy_liest_nie_die_marke_des_servers():
    assert ad.PENDING_FILE == os.environ.get("DEPLOY_PENDING_FILE")
