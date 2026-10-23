import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))


def _load(name: str, modname: str | None = None):
    """Lädt sandbox/<name>.py frisch per Pfad (wie es die Takte beim Start tun)."""
    spec = importlib.util.spec_from_file_location(modname or name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname or name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def datei(tmp_path, monkeypatch):
    """Eine Einstellungsdatei in tmp_path; AGENT_SETTINGS zeigt darauf, andere Quellen sind leer."""
    p = tmp_path / "agent-settings.json"
    monkeypatch.setenv("AGENT_SETTINGS", str(p))
    for var in ("REVIEW_MAX_TOKENS", "REVIEW_LLM_TIMEOUT", "REVIEW_MODEL_BIG", "REVIEW_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return p


def _schreibe(p: Path, werte) -> None:
    p.write_text(json.dumps(werte), encoding="utf-8")


# ---- agent_settings: Prüfung, Lesen, Schreiben ----

def test_die_feste_liste_der_einstellungen():
    s = _load("agent_settings")
    assert sorted(s.SPECS) == ["LITELLM_MODEL", "LITELLM_MODEL_BILLIG", "MAX_ROUNDS", "MAX_TRIES",
                               "MISSING_CHECK_GRACE_S",
                               "REVIEW_LLM_TIMEOUT", "REVIEW_MAX_TOKENS", "REVIEW_MODEL_BIG"]
    for name, spec in s.SPECS.items():
        assert "vorgabe" in spec and "text" in spec, name


@pytest.mark.parametrize("name,wert", [
    ("REVIEW_MAX_TOKENS", 32000), ("REVIEW_MAX_TOKENS", 1000), ("REVIEW_MAX_TOKENS", 100000),
    ("MAX_ROUNDS", 1), ("MAX_ROUNDS", 5), ("MAX_TRIES", 3), ("REVIEW_LLM_TIMEOUT", 600),
    ("MISSING_CHECK_GRACE_S", 60), ("MISSING_CHECK_GRACE_S", 3600),
    ("REVIEW_MODEL_BIG", "coder-glm"), ("REVIEW_MODEL_BIG", "longctx-lite"), ("REVIEW_MODEL_BIG", "longctx"),
    ("LITELLM_MODEL", "coder"), ("LITELLM_MODEL", "coder-nvidia"), ("LITELLM_MODEL", "coder-qwen"),
])
def test_validate_nimmt_gueltige_werte(name, wert):
    s = _load("agent_settings")
    assert s.validate(name, wert) == wert


@pytest.mark.parametrize("name,wert", [
    ("REVIEW_MAX_TOKENS", 999), ("REVIEW_MAX_TOKENS", 100001), ("REVIEW_MAX_TOKENS", "32000"),
    ("REVIEW_MAX_TOKENS", 32000.5), ("REVIEW_MAX_TOKENS", True), ("MAX_ROUNDS", 0), ("MAX_ROUNDS", 6),
    ("MAX_TRIES", -1), ("MISSING_CHECK_GRACE_S", 59), ("REVIEW_LLM_TIMEOUT", 901),
    ("REVIEW_MODEL_BIG", "coder-fast"), ("REVIEW_MODEL_BIG", ""), ("REVIEW_MODEL_BIG", 7),
    ("REVIEW_MODEL", "coder-glm"), ("AUTO_MERGE", 1), ("manual_paths", ["x"]),
])
def test_validate_lehnt_ungueltiges_ab(name, wert):
    s = _load("agent_settings")
    with pytest.raises(s.SettingError):
        s.validate(name, wert)


def test_load_ohne_datei_oder_mit_kaputter_datei_ist_leer(datei):
    s = _load("agent_settings")
    assert s.load() == {}                                  # Datei fehlt
    datei.write_text("{ kein json", encoding="utf-8")
    assert s.load() == {}
    _schreibe(datei, ["liste"])
    assert s.load() == {}


def test_load_behaelt_nur_gueltige_bekannte_werte(datei):
    s = _load("agent_settings")
    _schreibe(datei, {"MAX_ROUNDS": 3, "MAX_TRIES": 99, "REVIEW_MAX_TOKENS": "viel", "unbekannt": 1,
                      "REVIEW_MODEL_BIG": "longctx-lite"})
    assert s.load() == {"MAX_ROUNDS": 3, "REVIEW_MODEL_BIG": "longctx-lite"}


def test_value_nimmt_datei_vor_umgebung_vor_vorgabe(datei, monkeypatch):
    s = _load("agent_settings")
    assert s.value("REVIEW_MAX_TOKENS", 16000) == 16000        # nichts gesetzt: Vorgabe des Aufrufers
    monkeypatch.setenv("REVIEW_MAX_TOKENS", "9000")
    assert s.value("REVIEW_MAX_TOKENS", 16000) == 9000         # Umgebung
    _schreibe(datei, {"REVIEW_MAX_TOKENS": 24000})
    assert s.value("REVIEW_MAX_TOKENS", 16000) == 24000        # Datei gewinnt
    _schreibe(datei, {"REVIEW_MAX_TOKENS": 5})                 # ungültig in der Datei: Umgebung gilt
    assert s.value("REVIEW_MAX_TOKENS", 16000) == 9000


def test_value_liest_die_datei_bei_jedem_aufruf_neu(datei):
    s = _load("agent_settings")
    _schreibe(datei, {"MAX_ROUNDS": 3})
    assert s.value("MAX_ROUNDS", 2) == 3
    _schreibe(datei, {"MAX_ROUNDS": 4})
    assert s.value("MAX_ROUNDS", 2) == 4


def test_save_schreibt_geprueft_und_ohne_halbe_dateien(datei):
    s = _load("agent_settings")
    s.save({"MAX_ROUNDS": 3})
    assert json.loads(datei.read_text(encoding="utf-8")) == {"MAX_ROUNDS": 3}
    s.save({"REVIEW_MAX_TOKENS": 32000})                       # ergänzt, überschreibt nichts anderes
    assert json.loads(datei.read_text(encoding="utf-8")) == {"MAX_ROUNDS": 3, "REVIEW_MAX_TOKENS": 32000}
    assert [p.name for p in datei.parent.iterdir()] == [datei.name]   # keine .tmp-Reste


def test_save_mit_ungueltigem_wert_aendert_die_datei_nicht(datei):
    s = _load("agent_settings")
    s.save({"MAX_ROUNDS": 3})
    vorher = datei.read_text(encoding="utf-8")
    with pytest.raises(s.SettingError):
        s.save({"MAX_TRIES": 3, "MAX_ROUNDS": 99})
    assert datei.read_text(encoding="utf-8") == vorher


# ---- die Takte übernehmen die Werte ----

def test_review_tick_uebernimmt_die_werte_aus_der_datei(datei):
    _schreibe(datei, {"MAX_ROUNDS": 3, "REVIEW_MAX_TOKENS": 24000, "MISSING_CHECK_GRACE_S": 300,
                      "REVIEW_LLM_TIMEOUT": 600, "REVIEW_MODEL_BIG": "coder-glm"})
    r = _load("review_tick")
    assert r.MAX_ROUNDS == 3 and r.MAX_TOKENS == 24000 and r.MISSING_CHECK_GRACE_S == 300
    assert r.LLM_TIMEOUT == 600 and r.REVIEW_MODEL_BIG == "coder-glm"


def test_review_tick_ohne_datei_behaelt_die_vorgaben(datei):
    r = _load("review_tick")
    assert r.MAX_ROUNDS == 2 and r.MAX_TOKENS == 16000 and r.MISSING_CHECK_GRACE_S == 600
    assert r.LLM_TIMEOUT == 420


def test_review_tick_ignoriert_ungueltige_werte_in_der_datei(datei):
    _schreibe(datei, {"MAX_ROUNDS": 99, "REVIEW_MAX_TOKENS": "viel", "REVIEW_MODEL_BIG": "gibt-es-nicht"})
    r = _load("review_tick")
    assert r.MAX_ROUNDS == 2 and r.MAX_TOKENS == 16000
    assert r.REVIEW_MODEL_BIG != "gibt-es-nicht"


def test_worker_tick_uebernimmt_max_tries_aus_der_datei(datei):
    _schreibe(datei, {"MAX_TRIES": 4})
    w = _load("worker_tick")
    assert w.MAX_TRIES == 4


def test_worker_tick_ohne_datei_behaelt_die_vorgabe(datei):
    w = _load("worker_tick")
    assert w.MAX_TRIES == 2


# ---- agent_settings.aktuelle_werte: Anzeige für das Dashboard ----

def test_aktuelle_werte_nennen_wert_und_quelle(datei, monkeypatch):
    s = _load("agent_settings")
    alle = s.aktuelle_werte()
    assert sorted(alle) == sorted(s.SPECS)
    assert alle["MAX_ROUNDS"] == {"wert": 2, "quelle": "vorgabe"}
    assert alle["REVIEW_MAX_TOKENS"] == {"wert": 16000, "quelle": "vorgabe"}
    monkeypatch.setenv("REVIEW_MAX_TOKENS", "9000")
    assert s.aktuelle_werte()["REVIEW_MAX_TOKENS"] == {"wert": 9000, "quelle": "umgebung"}
    _schreibe(datei, {"REVIEW_MAX_TOKENS": 24000, "MAX_ROUNDS": 3})
    alle = s.aktuelle_werte()
    assert alle["REVIEW_MAX_TOKENS"] == {"wert": 24000, "quelle": "datei"}
    assert alle["MAX_ROUNDS"] == {"wert": 3, "quelle": "datei"}


def test_aktuelle_werte_ignorieren_ungueltiges_in_der_datei(datei):
    s = _load("agent_settings")
    _schreibe(datei, {"MAX_ROUNDS": 99})
    assert s.aktuelle_werte()["MAX_ROUNDS"] == {"wert": 2, "quelle": "vorgabe"}
