"""Randfälle für `sandbox/core/benchmark_providers`, die die Akzeptanztests nicht abdecken.

Alle Tests arbeiten offline: Die Datei liegt in `tmp_path`, die Umgebung ist ein einfaches
`dict`. Keine echten Umgebungsvariablen, kein Netzwerk, kein `gh`.
"""

import json

import pytest

from sandbox.core import benchmark_providers as bp
from sandbox.core.benchmark_providers import ProviderFehler

LITELLM_ENV = {"LITELLM": "http://proxy.invalid:4000", "LITELLM_BENCHMARK_KEY": "kb-1"}
OPENAI = {
    "name": "anbieter",
    "art": "openai-kompatibel",
    "base_url": "https://api.example.invalid/v1",
    "modell": "modell-1",
    "api_key_umgebung": "ANBIETER_API_KEY",
}
ALIAS = {"name": "alias", "art": "litellm-alias", "alias": "coder-glm"}


def _pfad(tmp_path, eintraege, name="liste.json"):
    pfad = tmp_path / name
    pfad.write_text(json.dumps({"providers": eintraege}), encoding="utf-8")
    return str(pfad)


def _roh(tmp_path, inhalt, name="roh.json"):
    pfad = tmp_path / name
    pfad.write_text(inhalt, encoding="utf-8")
    return str(pfad)


def test_openai_ohne_extra_body_bekommt_ein_leeres_dict(tmp_path):
    aktiv, _ = bp.load_providers(_pfad(tmp_path, [OPENAI]), {"ANBIETER_API_KEY": "k"})
    assert aktiv[0].extra_body == {}


def test_extra_body_wird_kopiert(tmp_path):
    eintrag = dict(OPENAI, extra_body={"a": 1})
    aktiv, _ = bp.load_providers(_pfad(tmp_path, [eintrag]), {"ANBIETER_API_KEY": "k"})
    assert aktiv[0].extra_body == {"a": 1}
    assert aktiv[0].extra_body is not eintrag["extra_body"]


def test_dateireihenfolge_bleibt_erhalten_und_uebersprungene_stehen_in_ihrer_reihenfolge(tmp_path):
    eintraege = [
        dict(OPENAI, name="erst"),
        dict(ALIAS, name="zweit"),
        dict(OPENAI, name="dritt", api_key_umgebung="DRITT_API_KEY"),
    ]
    aktiv, uebersprungen = bp.load_providers(
        _pfad(tmp_path, eintraege), dict(LITELLM_ENV, ANBIETER_API_KEY="k")
    )
    assert [p.name for p in aktiv] == ["erst", "zweit"]
    assert uebersprungen == [{"name": "dritt", "grund": "DRITT_API_KEY nicht gesetzt"}]


def test_pruefung_der_eintraege_kommt_vor_der_schluesselaufloesung(tmp_path):
    # Fehlender Schlüssel würde den Eintrag nur überspringen; das kaputte Feld bricht ab.
    eintrag = dict(OPENAI, modell="")
    with pytest.raises(ProviderFehler) as fehler:
        bp.load_providers(_pfad(tmp_path, [eintrag]), {})
    assert "modell" in str(fehler.value)


def test_litellm_adresse_ohne_schraegstrich_ergibt_genau_ein_v1(tmp_path):
    aktiv, _ = bp.load_providers(_pfad(tmp_path, [ALIAS]), LITELLM_ENV)
    assert aktiv[0].base_url == "http://proxy.invalid:4000/v1"


def test_base_url_entfernt_alle_abschliessenden_schraegstriche(tmp_path):
    eintrag = dict(OPENAI, base_url="https://api.example.invalid/v1///")
    aktiv, _ = bp.load_providers(_pfad(tmp_path, [eintrag]), {"ANBIETER_API_KEY": "k"})
    assert aktiv[0].base_url == "https://api.example.invalid/v1"


def test_at_im_pfad_ist_erlaubt_nur_zugangsdaten_im_host_nicht(tmp_path):
    gut = dict(OPENAI, base_url="https://api.example.invalid/open@ai/v1")
    aktiv, _ = bp.load_providers(_pfad(tmp_path, [gut]), {"ANBIETER_API_KEY": "k"})
    assert aktiv[0].base_url == "https://api.example.invalid/open@ai/v1"

    schlecht = dict(OPENAI, name="mit-zugang", base_url="https://nutzer:pw@api.example.invalid/v1")
    with pytest.raises(ProviderFehler):
        bp.load_providers(_pfad(tmp_path, [schlecht]), {"ANBIETER_API_KEY": "k"})


@pytest.mark.parametrize("base_url", ["", "https://", "http://", "https:///v1", "gemini://host/v1"])
def test_base_url_ohne_host_oder_falsches_schema_bricht_ab(tmp_path, base_url):
    with pytest.raises(ProviderFehler):
        bp.load_providers(_pfad(tmp_path, [dict(OPENAI, base_url=base_url)]), {})


def test_name_erlaubt_die_vorgesehenen_zeichen(tmp_path):
    for name in ("a", "A.1_b:c-d", "9"):
        aktiv, _ = bp.load_providers(_pfad(tmp_path, [dict(OPENAI, name=name)]), {"ANBIETER_API_KEY": "k"})
        assert aktiv[0].name == name


@pytest.mark.parametrize("name", ["a b", "ä", "a/b", "a+b", "a\nb"])
def test_name_mit_fremden_zeichen_bricht_ab(tmp_path, name):
    with pytest.raises(ProviderFehler):
        bp.load_providers(_pfad(tmp_path, [dict(OPENAI, name=name)]), {})


def test_doppelter_name_gilt_auch_ueber_verschiedene_arten(tmp_path):
    eintraege = [dict(OPENAI, name="gleich"), dict(ALIAS, name="gleich")]
    with pytest.raises(ProviderFehler) as fehler:
        bp.load_providers(_pfad(tmp_path, eintraege), LITELLM_ENV)
    assert "Eintrag 2" in str(fehler.value)


@pytest.mark.parametrize("eintraege", [[5], [None], [[]], ["x"]])
def test_eintrag_der_kein_objekt_ist_bricht_ab(tmp_path, eintraege):
    with pytest.raises(ProviderFehler) as fehler:
        bp.load_providers(_pfad(tmp_path, eintraege), {})
    assert "Eintrag 1" in str(fehler.value)


@pytest.mark.parametrize("wurzel", [[], "text", 5, None, {"providers": "x"}, {"providers": None}])
def test_wurzel_und_providers_muessen_die_richtige_form_haben(tmp_path, wurzel):
    with pytest.raises(ProviderFehler):
        bp.load_providers(_roh(tmp_path, json.dumps(wurzel)), {})


def test_weitere_schluessel_auf_oberster_ebene_stoeren_nicht(tmp_path):
    inhalt = json.dumps({"hinweis": "egal", "providers": [ALIAS]})
    aktiv, _ = bp.load_providers(_roh(tmp_path, inhalt), LITELLM_ENV)
    assert [p.name for p in aktiv] == ["alias"]


def test_verzeichnis_statt_datei_bricht_ab(tmp_path):
    with pytest.raises(ProviderFehler):
        bp.load_providers(str(tmp_path), {})


def test_meldungen_nennen_nummer_und_feld_aber_keinen_wert(tmp_path):
    geheim = "sk-DARF-NICHT-ERSCHEINEN-4711"
    faelle = [
        dict(OPENAI, api_key=geheim),
        dict(OPENAI, name=geheim + "!"),
        dict(OPENAI, modell=""),
        dict(OPENAI, base_url=f"https://{geheim}@host.invalid/v1"),
        dict(OPENAI, api_key_umgebung="klein"),
        dict(OPENAI, art="unbekannt"),
    ]
    for eintrag in faelle:
        with pytest.raises(ProviderFehler) as fehler:
            bp.load_providers(
                _pfad(tmp_path, [ALIAS, eintrag]), dict(LITELLM_ENV, ANBIETER_API_KEY=geheim)
            )
        meldung = str(fehler.value)
        assert meldung.startswith("Eintrag 2:"), meldung
        assert geheim not in meldung, meldung


def test_Proxy_und_json_fehler_nennen_keinen_schluessel(tmp_path):
    geheim = "kb-GEHEIM-77"
    env = dict(LITELLM_ENV, LITELLM_BENCHMARK_KEY=geheim)
    with pytest.raises(ProviderFehler) as fehler:
        bp.load_providers(_roh(tmp_path, "{kein json " + geheim), env)
    assert geheim not in str(fehler.value)


def test_provider_ist_hashbar_und_vergleicht_ohne_schluessel():
    a = bp.Provider(name="a", art="litellm-alias", base_url="u", modell="m", api_key="eins")
    b = bp.Provider(name="a", art="litellm-alias", base_url="u", modell="m", api_key="zwei")
    assert a == b
    assert len({a, b}) == 1
    assert hash(a) == hash(b)


def test_ohne_umgebungsobjekt_zaehlt_os_environ(monkeypatch, tmp_path):
    eintrag = dict(OPENAI, base_url="https://api.example.invalid/v1")
    datei = _pfad(tmp_path, [eintrag], "os.json")
    monkeypatch.setenv("AGENT_BENCHMARK_PROVIDERS", datei)
    monkeypatch.setenv("ANBIETER_API_KEY", "k")
    aktiv, _ = bp.load_providers()
    assert [p.name for p in aktiv] == ["anbieter"]


def _wert(**gesetzt):
    aufrufe = []

    def wert(name, vorgabe):
        aufrufe.append((name, vorgabe))
        return gesetzt.get(name, vorgabe)

    wert.aufrufe = aufrufe
    return wert


def test_aktuelle_provider_fragt_nur_die_grossen_aliase_ueber_wert():
    wert = _wert()
    bp.aktuelle_provider(wert, LITELLM_ENV)
    assert [name for name, _ in wert.aufrufe] == ["LITELLM_MODEL", "REVIEW_MODEL_BIG"]
    assert len(wert.aufrufe) == 2


def test_aktuelle_provider_fasst_worker_und_review_zusammen():
    env = dict(LITELLM_ENV, LITELLM_MODEL="gleich", REVIEW_MODEL="gleich")
    wert = _wert(REVIEW_MODEL_BIG="lang")
    aktiv, _ = bp.aktuelle_provider(wert, env)
    assert [(p.name, p.rolle) for p in aktiv] == [
        ("gleich", "worker+review"),
        ("lang", "review_gross"),
    ]


def test_aktuelle_provider_meldet_jeden_fehlenden_alias_einmal_in_reihenfolge():
    env = {"REVIEW_MODEL": "rev", "LITELLM_MODEL": "work"}
    aktiv, uebersprungen = bp.aktuelle_provider(_wert(REVIEW_MODEL_BIG="gross"), env)
    assert aktiv == []
    grund = "LITELLM oder LITELLM_BENCHMARK_KEY nicht gesetzt"
    assert uebersprungen == [
        {"name": "work", "grund": grund},
        {"name": "rev", "grund": grund},
        {"name": "gross", "grund": grund},
    ]


def test_aktuelle_provider_liefert_rollen_und_gleiche_adresse_wie_die_datei(tmp_path):
    aktiv, _ = bp.aktuelle_provider(_wert(), LITELLM_ENV)
    datei, _ = bp.load_providers(_pfad(tmp_path, [ALIAS]), LITELLM_ENV)
    for p in aktiv:
        assert (p.art, p.base_url, p.extra_body) == (
            datei[0].art,
            datei[0].base_url,
            datei[0].extra_body,
        )
