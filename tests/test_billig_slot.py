"""Billig-Slot (#312): Einstellung, freie Aliase und die Fallbackkette ohne bezahlte Modelle.

Die Akzeptanztests aus #312 prüfen das Verhalten der Takte; hier stehen die Einzelteile, die
dort nur mitgeprüft werden: die erlaubt-Liste der Einstellung und `billig_fallbacks()`.
"""

import importlib.util
import itertools
import json
import sys
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

import agent_settings  # noqa: E402

# Vertrag aus #312: freie Aliase in dieser Reihenfolge; sie sind die Kette des Billig-Slots.
FREIE_ALIASE = ["coder-mimo-free", "coder-deepseek-free", "coder-nemotron-free",
                "coder-minimax-free", "coder-beam-free"]

_ZAEHLER = itertools.count()


@pytest.fixture
def datei(tmp_path, monkeypatch):
    """Einstellungsdatei in tmp_path; die Umgebungsvariable der Einstellung ist leer."""
    p = tmp_path / "agent-settings.json"
    monkeypatch.setenv("AGENT_SETTINGS", str(p))
    monkeypatch.delenv("LITELLM_MODEL_BILLIG", raising=False)
    return p


def _tick(monkeypatch, slot="2"):
    """worker_tick frisch per Pfad laden (wie der Takt beim Start)."""
    monkeypatch.setenv("WORKER_SLOT", slot)
    name = f"worker_tick_billig_einzelt_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / "worker_tick.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- Einstellung LITELLM_MODEL_BILLIG ----

def test_specs_eintrag_nennt_genau_die_freien_aliase():
    spec = agent_settings.SPECS["LITELLM_MODEL_BILLIG"]
    assert spec["erlaubt"] == FREIE_ALIASE
    assert spec["vorgabe"] is None


@pytest.mark.parametrize("wert", FREIE_ALIASE)
def test_validate_nimmt_jeden_freien_alias(wert):
    assert agent_settings.validate("LITELLM_MODEL_BILLIG", wert) == wert


@pytest.mark.parametrize("wert", ["coder", "coder-go", "coder-or", "coder-glm", "", "coder-nichtda",
                                 None, 7, True])
def test_validate_lehnt_fremde_aliase_ab(wert):
    """Ein Tippfehler darf den Billig-Slot nie auf ein bezahltes Modell schalten."""
    with pytest.raises(agent_settings.SettingError):
        agent_settings.validate("LITELLM_MODEL_BILLIG", wert)


def test_value_ohne_quelle_liefert_die_vorgabe_none(datei):
    assert agent_settings.value("LITELLM_MODEL_BILLIG", None) is None


def test_value_liest_die_umgebung_roh_wie_review_model_big(datei, monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL_BILLIG", "coder-mimo-free")
    assert agent_settings.value("LITELLM_MODEL_BILLIG", None) == "coder-mimo-free"
    # Wie REVIEW_MODEL_BIG: Die Umgebung gilt unverändert, die Prüfung passiert erst beim Verbrauch.
    monkeypatch.setenv("LITELLM_MODEL_BILLIG", "coder-gibt-es-nicht")
    assert agent_settings.value("LITELLM_MODEL_BILLIG", None) == "coder-gibt-es-nicht"


def test_value_datei_gewinnt_vor_umgebung_und_verwirft_ungueltiges(datei, monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL_BILLIG", "coder-beam-free")
    datei.write_text(json.dumps({"LITELLM_MODEL_BILLIG": "coder-mimo-free"}), encoding="utf-8")
    assert agent_settings.value("LITELLM_MODEL_BILLIG", None) == "coder-mimo-free"
    datei.write_text(json.dumps({"LITELLM_MODEL_BILLIG": "coder-go"}), encoding="utf-8")
    assert agent_settings.value("LITELLM_MODEL_BILLIG", None) == "coder-beam-free"


# ---- billig_fallbacks() ----

def test_billig_fallbacks_ist_die_erlaubt_liste_ohne_primary(monkeypatch, tmp_path):
    w = _tick(monkeypatch)
    assert w.billig_fallbacks("coder-mimo-free") == FREIE_ALIASE[1:]
    assert w.billig_fallbacks("coder-beam-free") == FREIE_ALIASE[:-1]
    assert w.billig_fallbacks("coder-nemotron-free") == ["coder-mimo-free", "coder-deepseek-free",
                                                         "coder-minimax-free", "coder-beam-free"]


@pytest.mark.parametrize("primary", [None, "", "coder", "coder-go", "coder-or", "coder-nichtda"])
def test_billig_fallbacks_ohne_gueltigen_primary_ist_leer(primary, monkeypatch, tmp_path):
    w = _tick(monkeypatch)
    assert w.billig_fallbacks(primary) == []


def test_billig_fallbacks_enthaelt_kein_bezahltes_und_kein_abo_modell(monkeypatch, tmp_path):
    w = _tick(monkeypatch)
    kette = w.billig_fallbacks("coder-mimo-free")
    assert not set(kette) & set(w.BEZAHLTE_MODELLE)
    assert not {"coder-go", "coder-pro-go", "coder-kimi-go"} & set(kette)


# ---- Slot ohne Einstellung bleibt ein normaler Slot ----

def test_slot_2_ohne_einstellung_bleibt_normal(monkeypatch, tmp_path):
    nicht_da = str(tmp_path / "nicht_vorhanden.json")
    monkeypatch.delenv("AGENT_SETTINGS", raising=False)
    monkeypatch.delenv("LITELLM_MODEL_BILLIG", raising=False)
    monkeypatch.setattr(agent_settings, "VORGABE_PFAD", nicht_da)
    monkeypatch.setenv("AGENT_SETTINGS", nicht_da)
    w = _tick(monkeypatch)
    assert w.BILLIG_MODEL is None
    assert w.IS_BILLIG_SLOT is False
    assert w.ACTIVE_MODEL != "coder-mimo-free"


def test_einstellung_ohne_slot_2_schaltet_keinen_billig_modus(monkeypatch, tmp_path):
    datei = tmp_path / "agent-settings.json"
    datei.write_text(json.dumps({"LITELLM_MODEL_BILLIG": "coder-mimo-free"}), encoding="utf-8")
    monkeypatch.setenv("AGENT_SETTINGS", str(datei))
    w = _tick(monkeypatch, slot="1")
    assert w.BILLIG_MODEL == "coder-mimo-free"
    assert w.IS_BILLIG_SLOT is False
