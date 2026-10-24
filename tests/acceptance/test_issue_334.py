import json
from dataclasses import FrozenInstanceError

import pytest

from sandbox.core import benchmark_providers as bp
from sandbox.core.benchmark_providers import Provider, ProviderFehler

LITELLM_ENV = {"LITELLM": "http://proxy.invalid:4000/", "LITELLM_BENCHMARK_KEY": "kb-123"}
OPENAI = {
    "name": "groq",
    "art": "openai-kompatibel",
    "base_url": "https://api.example.invalid/openai/v1/",
    "modell": "modell-1",
    "api_key_umgebung": "GROQ_API_KEY",
}
ALIAS = {"name": "glm", "art": "litellm-alias", "alias": "coder-glm"}


def _datei(tmp_path, eintraege, name="liste.json"):
    pfad = tmp_path / name
    pfad.write_text(json.dumps({"providers": eintraege}), encoding="utf-8")
    return str(pfad)


def test_openai_kompatibel_wird_aufgeloest(tmp_path):
    eintrag = dict(OPENAI, extra_body={"temperature_hint": 1})
    aktiv, uebersprungen = bp.load_providers(_datei(tmp_path, [eintrag]), {"GROQ_API_KEY": "geheim-1"})
    assert uebersprungen == []
    assert len(aktiv) == 1
    p = aktiv[0]
    assert (p.name, p.art, p.modell, p.rolle) == ("groq", "openai-kompatibel", "modell-1", "")
    assert p.base_url == "https://api.example.invalid/openai/v1"
    assert p.api_key == "geheim-1"
    assert p.extra_body == {"temperature_hint": 1}


@pytest.mark.parametrize("env", [{}, {"GROQ_API_KEY": ""}])
def test_fehlender_schluessel_ueberspringt_nur_diesen_eintrag(tmp_path, env):
    aktiv, uebersprungen = bp.load_providers(_datei(tmp_path, [OPENAI, ALIAS]), dict(env, **LITELLM_ENV))
    assert [p.name for p in aktiv] == ["glm"]
    assert uebersprungen == [{"name": "groq", "grund": "GROQ_API_KEY nicht gesetzt"}]


def test_litellm_alias_bildet_basisadresse_und_schluessel(tmp_path):
    aktiv, uebersprungen = bp.load_providers(_datei(tmp_path, [ALIAS]), LITELLM_ENV)
    assert uebersprungen == []
    p = aktiv[0]
    assert p.art == "litellm-alias"
    assert p.base_url == "http://proxy.invalid:4000/v1"
    assert p.modell == "coder-glm"
    assert p.api_key == "kb-123"
    assert p.extra_body == {"disable_fallbacks": True}


@pytest.mark.parametrize("env", [
    {},
    {"LITELLM": "http://proxy.invalid:4000"},
    {"LITELLM_BENCHMARK_KEY": "kb-123"},
    {"LITELLM": "http://proxy.invalid:4000", "LITELLM_BENCHMARK_KEY": ""},
])
def test_litellm_ohne_adresse_oder_schluessel_wird_uebersprungen(tmp_path, env):
    aktiv, uebersprungen = bp.load_providers(_datei(tmp_path, [ALIAS]), env)
    assert aktiv == []
    assert uebersprungen == [
        {"name": "glm", "grund": "LITELLM oder LITELLM_BENCHMARK_KEY nicht gesetzt"}
    ]


def test_leere_liste_ist_erlaubt(tmp_path):
    assert bp.load_providers(_datei(tmp_path, []), {}) == ([], [])


def test_pfad_vorrang_argument_vor_umgebung_vor_vorgabe(tmp_path, monkeypatch):
    a = _datei(tmp_path, [dict(ALIAS, name="aus-argument")], "a.json")
    b = _datei(tmp_path, [dict(ALIAS, name="aus-umgebung")], "b.json")
    c = _datei(tmp_path, [dict(ALIAS, name="aus-vorgabe")], "c.json")
    monkeypatch.setattr(bp, "DEFAULT_PFAD", c)
    env = dict(LITELLM_ENV, AGENT_BENCHMARK_PROVIDERS=b)
    assert [p.name for p in bp.load_providers(a, env)[0]] == ["aus-argument"]
    assert [p.name for p in bp.load_providers(None, env)[0]] == ["aus-umgebung"]
    assert [p.name for p in bp.load_providers(None, LITELLM_ENV)[0]] == ["aus-vorgabe"]


def test_standardpfad_ist_etc_agent_benchmark_providers():
    assert bp.DEFAULT_PFAD == "/etc/agent-benchmark-providers.json"


def _roh(tmp_path, text):
    pfad = tmp_path / "roh.json"
    pfad.write_text(text, encoding="utf-8")
    return str(pfad)


def test_fehlende_oder_kaputte_datei_bricht_ab(tmp_path):
    with pytest.raises(ProviderFehler):
        bp.load_providers(str(tmp_path / "gibt-es-nicht.json"), {})
    for text in ("{kein json", "[]", '{"andere": []}', '{"providers": {}}', '{"providers": ["x"]}'):
        with pytest.raises(ProviderFehler):
            bp.load_providers(_roh(tmp_path, text), {})


@pytest.mark.parametrize("eintrag", [
    {k: v for k, v in OPENAI.items() if k != "name"},
    dict(OPENAI, name=""),
    dict(OPENAI, name="mit leerzeichen"),
    dict(OPENAI, name=5),
    {k: v for k, v in OPENAI.items() if k != "art"},
    dict(OPENAI, art="unbekannt"),
    {k: v for k, v in ALIAS.items() if k != "alias"},
    dict(ALIAS, alias=""),
    dict(ALIAS, alias=3),
    {k: v for k, v in OPENAI.items() if k != "base_url"},
    dict(OPENAI, base_url="ftp://host.invalid/v1"),
    dict(OPENAI, base_url="https://nutzer:pw@host.invalid/v1"),
    {k: v for k, v in OPENAI.items() if k != "modell"},
    dict(OPENAI, modell=""),
    {k: v for k, v in OPENAI.items() if k != "api_key_umgebung"},
    dict(OPENAI, api_key_umgebung="klein"),
    dict(OPENAI, api_key_umgebung="MIT-STRICH"),
    dict(OPENAI, extra_body=["kein", "dict"]),
])
def test_ungueltiger_eintrag_bricht_die_ganze_liste_ab(tmp_path, eintrag):
    with pytest.raises(ProviderFehler):
        bp.load_providers(_datei(tmp_path, [ALIAS, eintrag]), LITELLM_ENV)


def test_doppelter_name_bricht_ab(tmp_path):
    with pytest.raises(ProviderFehler):
        bp.load_providers(_datei(tmp_path, [ALIAS, dict(OPENAI, name="glm")]), LITELLM_ENV)


def test_klartext_schluessel_in_der_datei_wird_abgelehnt_ohne_den_wert_zu_nennen(tmp_path):
    eintrag = dict(OPENAI, api_key="sk-GEHEIMER-WERT-999")
    with pytest.raises(ProviderFehler) as e:
        bp.load_providers(_datei(tmp_path, [ALIAS, eintrag]), {"GROQ_API_KEY": "geheim-1"})
    meldung = str(e.value)
    assert "api_key" in meldung
    assert "Eintrag 2" in meldung
    assert "GEHEIMER-WERT" not in meldung
    assert "geheim-1" not in meldung


def test_provider_zeigt_den_schluessel_nicht_und_ist_unveraenderlich(tmp_path):
    p = bp.load_providers(_datei(tmp_path, [OPENAI]), {"GROQ_API_KEY": "geheim-1"})[0][0]
    assert "geheim-1" not in repr(p)
    with pytest.raises(FrozenInstanceError):
        p.name = "anders"
    a = Provider(name="a", art="x", base_url="b", modell="m", api_key="1")
    assert a == Provider(name="a", art="x", base_url="b", modell="m", api_key="2")


def _wert(**gesetzt):
    aufrufe = []

    def wert(name, vorgabe):
        aufrufe.append((name, vorgabe))
        return gesetzt.get(name, vorgabe)

    wert.aufrufe = aufrufe
    return wert


def test_aktuelle_provider_mit_vorgaben_fasst_gleiche_aliase_zusammen():
    wert = _wert(LITELLM_MODEL="coder-x")
    aktiv, uebersprungen = bp.aktuelle_provider(wert, LITELLM_ENV)
    assert uebersprungen == []
    assert [(p.name, p.rolle) for p in aktiv] == [
        ("coder-x", "worker"),
        ("reviewer-go", "review+review_gross"),
    ]
    assert aktiv[0].art == "litellm-alias"
    assert aktiv[0].base_url == "http://proxy.invalid:4000/v1"
    assert aktiv[0].api_key == "kb-123"
    assert aktiv[0].extra_body == {"disable_fallbacks": True}
    assert ("LITELLM_MODEL", "coder") in wert.aufrufe
    assert ("REVIEW_MODEL_BIG", "reviewer-go") in wert.aufrufe


def test_aktuelle_provider_mit_drei_verschiedenen_aliasen():
    env = dict(LITELLM_ENV, REVIEW_MODEL="rev-a", LITELLM_MODEL="aus-env")
    wert = _wert(REVIEW_MODEL_BIG="longctx")
    aktiv, _ = bp.aktuelle_provider(wert, env)
    assert [(p.name, p.rolle) for p in aktiv] == [
        ("aus-env", "worker"),
        ("rev-a", "review"),
        ("longctx", "review_gross"),
    ]
    assert ("LITELLM_MODEL", "aus-env") in wert.aufrufe
    assert ("REVIEW_MODEL_BIG", "rev-a") in wert.aufrufe


def test_aktuelle_provider_worker_gleich_review_ergibt_einen_eintrag():
    env = dict(LITELLM_ENV, REVIEW_MODEL="gleich")
    aktiv, _ = bp.aktuelle_provider(_wert(LITELLM_MODEL="gleich"), env)
    assert [(p.name, p.rolle) for p in aktiv] == [("gleich", "worker+review+review_gross")]


def test_aktuelle_provider_ohne_schluessel_meldet_jeden_alias_einmal():
    aktiv, uebersprungen = bp.aktuelle_provider(_wert(), {})
    assert aktiv == []
    grund = "LITELLM oder LITELLM_BENCHMARK_KEY nicht gesetzt"
    assert uebersprungen == [
        {"name": "coder", "grund": grund},
        {"name": "reviewer-go", "grund": grund},
    ]
