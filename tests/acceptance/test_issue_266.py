import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_probecache", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_probecache"] = w
_spec.loader.exec_module(w)


def test_probe_cache_hit_nimmt_frischen_passenden_eintrag(monkeypatch):
    monkeypatch.setattr(w, "PROBE_CACHE_S", 600.0)
    state = {w.PROBE_CACHE_KEY: {"modell": "prim", "ok_at": 1000.0}}
    assert w.probe_cache_hit(state, "prim", now=1600.0) == "prim"


def test_probe_cache_hit_verwirft_abgelaufenen_eintrag(monkeypatch):
    monkeypatch.setattr(w, "PROBE_CACHE_S", 600.0)
    state = {w.PROBE_CACHE_KEY: {"modell": "prim", "ok_at": 1000.0}}
    assert w.probe_cache_hit(state, "prim", now=1601.0) is None


def test_probe_cache_hit_verwirft_fremdes_und_unlesbares(monkeypatch):
    monkeypatch.setattr(w, "PROBE_CACHE_S", 600.0)
    fremd = {w.PROBE_CACHE_KEY: {"modell": "anders", "ok_at": 1000.0}}
    assert w.probe_cache_hit(fremd, "prim", now=1100.0) is None
    assert w.probe_cache_hit({}, "prim", now=1100.0) is None
    for boese_eintraege in (None, [1], 7, "x"):
        kaputt = {w.PROBE_CACHE_KEY: boese_eintraege}
        assert w.probe_cache_hit(kaputt, "prim", now=1100.0) is None
    for fehlerhaft in ({"ok_at": None}, {"ok_at": "1000"}, {"ok_at": True}, {}):
        eintrag = dict({"modell": "prim"}, **fehlerhaft)
        assert w.probe_cache_hit({w.PROBE_CACHE_KEY: eintrag}, "prim", now=1100.0) is None


def test_probe_cache_hit_verwirft_zukünftiges_und_kein_string_pseudoid(monkeypatch):
    monkeypatch.setattr(w, "PROBE_CACHE_S", 600.0)
    zukunft = {w.PROBE_CACHE_KEY: {"modell": "prim", "ok_at": 2000.0}}
    assert w.probe_cache_hit(zukunft, "prim", now=1100.0) is None


def test_probe_cache_speichern_ersetzt_eintrag_ohne_zustandsspeichern(monkeypatch):
    state = {w.PROBE_CACHE_KEY: {"modell": "alter-modell", "ok_at": 1.0}}

    def kein_speichern(*args, **kw):
        raise AssertionError("probe_cache_speichern berührt die Datei selbst nicht")

    monkeypatch.setattr(w, "save_state", kein_speichern)
    w.probe_cache_speichern(state, "prim", now=500.0)
    assert state[w.PROBE_CACHE_KEY] == {"modell": "prim", "ok_at": 500.0}
