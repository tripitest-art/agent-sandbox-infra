import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _laden(name, datei):
    spec = importlib.util.spec_from_file_location(name, ROOT / "sandbox" / datei)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w = _laden("worker_tick_eskalation", "worker_tick.py")
r = _laden("review_tick_eskalation", "review_tick.py")


def test_zweiter_versuch_wechselt_die_familie():
    assert w.model_for_attempt(0, "coder-go") == "coder-go"
    assert w.model_for_attempt(1, "coder-go") == "coder-kimi-go"
    assert w.model_for_attempt(2, "coder-go") == "coder-pro-go"
    assert w.model_for_attempt(7, "coder-go") == "coder-pro-go"


def test_fremdes_basismodell_bleibt_fest():
    assert w.model_for_attempt(1, "coder") == "coder"
    assert w.model_for_attempt(1, "coder-qwen-go") == "coder-qwen-go"


def test_kimi_ist_eigene_familie_und_unabhaengig_vom_reviewer():
    name = w.worker_name("coder-kimi-go")
    assert r.worker_family(name) == "kimi"
    assert r.worker_family(name) != r.worker_family(w.worker_name("coder-go"))
    assert r.independence_problem(name, "reviewer-go") is None
