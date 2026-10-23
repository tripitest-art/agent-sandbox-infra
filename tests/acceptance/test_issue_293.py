import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"


def _laden(monkeypatch, name="worker_tick_reihenfolge"):
    monkeypatch.delenv("LITELLM_FALLBACKS", raising=False)
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    spec = importlib.util.spec_from_file_location(name, _PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_vorgabe_ist_go_dann_deepseek_dann_openrouter(monkeypatch):
    w = _laden(monkeypatch)
    assert w.LITELLM_FALLBACKS == ["coder-go", "coder", "coder-or"]
    assert "coder-free" not in w.LITELLM_FALLBACKS


def test_probe_geht_die_stufen_der_reihe_nach_durch(monkeypatch):
    w = _laden(monkeypatch)
    aufrufe = []

    def probe(modell):
        aufrufe.append(modell)
        return {"coder-go": "HTTP 429", "coder": "TimeoutError"}.get(modell)

    monkeypatch.setattr(w, "probe_model", probe)
    assert w.choose_model("coder-go") == ("coder-or", ["coder-go: HTTP 429", "coder: TimeoutError"])
    assert aufrufe == ["coder-go", "coder", "coder-or"]


def test_antwortet_go_wird_nur_eine_probe_gesendet(monkeypatch):
    w = _laden(monkeypatch)
    aufrufe = []
    monkeypatch.setattr(w, "probe_model", lambda m: aufrufe.append(m))
    assert w.choose_model("coder-go") == ("coder-go", [])
    assert aufrufe == ["coder-go"]


def test_pro_stufe_faellt_erst_auf_go_dann_auf_deepseek(monkeypatch):
    w = _laden(monkeypatch)
    aufrufe = []

    def probe(modell):
        aufrufe.append(modell)
        return None if modell == "coder" else "HTTP 429"

    monkeypatch.setattr(w, "probe_model", probe)
    modell, gescheitert = w.choose_model("coder-pro-go")
    assert modell == "coder"
    assert aufrufe == ["coder-pro-go", "coder-go", "coder"]
    assert gescheitert == ["coder-pro-go: HTTP 429", "coder-go: HTTP 429"]


def test_kein_modell_antwortet(monkeypatch):
    w = _laden(monkeypatch)
    monkeypatch.setattr(w, "probe_model", lambda m: "HTTP 503")
    modell, gescheitert = w.choose_model("coder-go")
    assert modell is None
    assert [g.split(":")[0] for g in gescheitert] == ["coder-go", "coder", "coder-or"]


@pytest.mark.parametrize("primary, modell, erwartet", [
    ("coder-go", "coder", "Ausweichmodell coder (DeepSeek-API) verbraucht Guthaben statt coder-go"),
    ("coder-go", "coder-or", "Ausweichmodell coder-or (OpenRouter) verbraucht Guthaben statt coder-go"),
    ("coder-pro-go", "coder", "Ausweichmodell coder (DeepSeek-API) verbraucht Guthaben statt coder-pro-go"),
    ("coder-go", "coder-go", None),
    ("coder", "coder", None),
    ("coder-pro-go", "coder-go", None),
    ("coder-go", "coder-kimi-go", None),
])
def test_hinweis_nur_beim_wechsel_auf_ein_bezahltes_modell(monkeypatch, primary, modell, erwartet):
    w = _laden(monkeypatch)
    assert w.bezahltes_ausweichen(primary, modell) == erwartet


def test_bezahlte_modelle_sind_deepseek_und_openrouter(monkeypatch):
    w = _laden(monkeypatch)
    assert w.BEZAHLTE_MODELLE == {"coder": "DeepSeek-API", "coder-or": "OpenRouter"}
