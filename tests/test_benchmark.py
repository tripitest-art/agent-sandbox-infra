"""Randfälle des Benchmark-Runners (#337).

Netz und Modellaufrufe laufen ausschließlich über Fakes; die Code-Bewertung führt wie im
Betrieb lokalen Modellcode in einem temporären Verzeichnis aus, ruft aber nie ein Modell.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sandbox.core import benchmark_code, benchmark_providers, benchmark_review

ROOT = Path(__file__).resolve().parents[1]
_PFAD = ROOT / "sandbox" / "benchmark.py"


def _laden():
    spec = importlib.util.spec_from_file_location("benchmark_randfall_test", _PFAD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ok(text, kosten=None, dauer=0.5):
    return {"ok": True, "text": text, "fehler": None, "ratenbegrenzt": False,
            "dauer_s": dauer, "usage": None, "kosten": kosten}


def _fehler(grund, rate=False):
    return {"ok": False, "text": "", "fehler": grund, "ratenbegrenzt": rate,
            "dauer_s": 0.25, "usage": None, "kosten": None}


def _richtige_antworten():
    antworten = {}
    for t in benchmark_code.CODE_TASKS:
        antworten[benchmark_code.build_code_prompt(t)] = "```python\n" + t["referenz"] + "\n```"
    for t in benchmark_review.REVIEW_TASKS:
        antworten[benchmark_review.build_review_prompt(t)] = t["referenz_antwort"]
    return antworten


class FakeChat:
    def __init__(self, antwort):
        self.aufrufe = []
        self._antwort = antwort

    def __call__(self, base_url, model, prompt, api_key="", timeout=120.0,
                 max_tokens=2048, temperature=0.0, extra_body=None):
        aufruf = {"base_url": base_url, "model": model, "prompt": prompt, "api_key": api_key,
                  "timeout": timeout, "extra_body": extra_body}
        self.aufrufe.append(aufruf)
        return self._antwort(prompt)


def _richtig():
    richtig = _richtige_antworten()
    return FakeChat(lambda prompt: _ok(richtig[prompt]))


def _provider(name="p1", rolle=""):
    return benchmark_providers.Provider(
        name=name, art="openai-kompatibel", base_url=f"https://{name}.example/v1",
        modell=f"{name}-modell", api_key="geheim-123456", extra_body={"x": 1}, rolle=rolle,
    )


REVIEW_TASK = benchmark_review.REVIEW_TASKS[0]


def _lauf(chat, code=None, review=None, **kw):
    b = _laden()
    kw.setdefault("schlafen", lambda s: None)
    kw.setdefault("jetzt", lambda: "2026-01-01T00:00:00Z")
    kw.setdefault("lauf_id", "L")
    return b.run_benchmark(
        [_provider()],
        [] if code is None else code,
        [REVIEW_TASK] if review is None else review,
        chat,
        **kw,
    )


def test_negativer_wiederholungswert_ist_ein_fehler():
    with pytest.raises(ValueError):
        _lauf(_richtig(), wiederholungen=-1)


def test_gesamtlimit_null_bricht_vor_dem_ersten_versuch_ab():
    chat = _richtig()
    zeilen, abgebrochen = _lauf(chat, gesamt_limit_s=0.0, wiederholungen=1)
    assert abgebrochen is True
    assert zeilen == [] and chat.aufrufe == []


def test_429_danach_anderer_fehler_zaehlt_nicht_als_ratenbegrenzt():
    folge = iter([_fehler("HTTP 429", rate=True), _fehler("HTTP 500")])
    chat = FakeChat(lambda prompt: next(folge))
    schlaf = []
    zeilen, abgebrochen = _lauf(chat, wiederholungen=1, pause_429_s=7.0, schlafen=schlaf.append)
    assert abgebrochen is False
    assert len(chat.aufrufe) == 2 and schlaf == [7.0]
    assert (zeilen[0]["bestanden"], zeilen[0]["grund"], zeilen[0]["ratenbegrenzt"]) == (
        False, "HTTP 500", False)


def test_uhr_wird_nicht_vor_dem_wiederholungsversuch_gelesen():
    gelesen = []

    def uhr():
        gelesen.append(1)
        return 0.0

    folge = iter([_fehler("HTTP 429", rate=True), _ok(REVIEW_TASK["referenz_antwort"])])
    _lauf(FakeChat(lambda prompt: next(folge)), wiederholungen=1, uhr=uhr)
    assert len(gelesen) == 2


def test_zeit_kommt_je_zeile_aus_jetzt():
    zeiten = iter(["2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z"])
    zeilen, _ = _lauf(_richtig(), wiederholungen=2, jetzt=lambda: next(zeiten))
    assert [z["zeit"] for z in zeilen] == ["2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z"]


def test_dauer_und_kosten_kommen_vom_letzten_aufruf():
    folge = iter([
        _fehler("HTTP 429", rate=True),
        _ok(REVIEW_TASK["referenz_antwort"], kosten=0.004, dauer=1.25),
    ])
    zeilen, _ = _lauf(FakeChat(lambda prompt: next(folge)), wiederholungen=1)
    assert zeilen[0]["bestanden"] is True
    assert zeilen[0]["dauer_s"] == 1.25 and zeilen[0]["kosten"] == 0.004


def test_zusammenfassung_leerer_lauf_ist_leer():
    assert _laden().zusammenfassung([]) == {}


def test_append_ergebnisse_haengt_an_bestehende_datei_an(tmp_path):
    pfad = tmp_path / "e.jsonl"
    pfad.write_text('{"alt": 1}\n', encoding="utf-8")
    _laden().append_ergebnisse(str(pfad), [{"g": "Änderungen nötig"}])
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["alt"] if "alt" in x else json.loads(x)["g"] for x in zeilen] == [
        1, "Änderungen nötig"]
    assert "\\u00c4" not in zeilen[1]


def _liste(tmp_path, eintraege):
    pfad = tmp_path / "anbieter.json"
    pfad.write_text(json.dumps({"providers": eintraege}), encoding="utf-8")
    return str(pfad)


def _eintrag(name="p1", env="P1_KEY", base_url=None, modell=None):
    return {"name": name, "art": "openai-kompatibel",
            "base_url": base_url or f"https://{name}.example/v1",
            "modell": modell or f"{name}-modell", "api_key_umgebung": env}


def _modellumgebung(monkeypatch):
    for n in ("LITELLM_MODEL", "REVIEW_MODEL", "REVIEW_MODEL_BIG"):
        monkeypatch.delenv(n, raising=False)


def test_main_nimmt_die_ergebnisdatei_aus_der_umgebung(tmp_path, monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    out = tmp_path / "aus-umgebung.jsonl"
    code = b.main(
        ["--providers", _liste(tmp_path, [_eintrag()]), "--wiederholungen", "1",
         "--ohne-aktuelle"],
        env={"P1_KEY": "x", "BENCHMARK_ERGEBNISSE": str(out)},
        chat=_richtig(), schlafen=lambda s: None,
    )
    assert code == 0 and out.exists()


def test_main_schreibfehler_ist_exit_1_und_druckt_die_zusammenfassung(tmp_path, capsys,
                                                                    monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    out = tmp_path / "fehlt" / "e.jsonl"
    code = b.main(
        ["--providers", _liste(tmp_path, [_eintrag()]), "--out", str(out),
         "--wiederholungen", "1", "--ohne-aktuelle"],
        env={"P1_KEY": "x"}, chat=_richtig(), schlafen=lambda s: None,
    )
    ausgabe = capsys.readouterr()
    n_code, n_review = len(benchmark_code.CODE_TASKS), len(benchmark_review.REVIEW_TASKS)
    assert code == 1
    assert f"p1: code {n_code}/{n_code} review {n_review}/{n_review} ratenbegrenzt 0" in ausgabe.out
    assert "Ergebnisdatei nicht schreibbar: FileNotFoundError" in ausgabe.err
    assert not out.exists()


@pytest.mark.parametrize("wert", ["0", "11", "abc"])
def test_main_lehnt_unbrauchbare_wiederholungen_ab(wert, monkeypatch):
    _modellumgebung(monkeypatch)
    with pytest.raises(SystemExit) as fehler:
        _laden().main(["--wiederholungen", wert], env={}, chat=_richtig())
    assert fehler.value.code == 2


def test_main_liste_gewinnt_bei_gleichem_namen(tmp_path, monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    liste = _liste(tmp_path, [
        _eintrag("coder", "P1_KEY", base_url="https://liste.example/v1", modell="liste-modell"),
    ])
    chat = _richtig()
    out = tmp_path / "e.jsonl"
    code = b.main(
        ["--providers", liste, "--out", str(out), "--wiederholungen", "1"],
        env={"P1_KEY": "x", "LITELLM": "http://llm.example:4000",
             "LITELLM_BENCHMARK_KEY": "y"},
        chat=chat, schlafen=lambda s: None,
    )
    assert code == 0
    zeilen = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert {z["anbieter"] for z in zeilen} == {"coder", "reviewer-go"}
    assert not [a for a in chat.aufrufe if a["model"] == "coder"]
    assert {a["base_url"] for a in chat.aufrufe if a["model"] == "liste-modell"} == {
        "https://liste.example/v1"}


def test_main_nur_aktuelle_schliesst_die_liste_aus(tmp_path, monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    with pytest.raises(SystemExit) as fehler:
        b.main(["--nur-aktuelle", "--providers", str(tmp_path / "x.json")], env={},
               chat=_richtig())
    assert fehler.value.code == 2
    with pytest.raises(SystemExit) as fehler:
        b.main(["--nur-aktuelle", "--ohne-aktuelle"], env={}, chat=_richtig())
    assert fehler.value.code == 2


def test_main_nur_aktuelle_ohne_schluessel_meldet_beide_aliase(capsys, monkeypatch):
    _modellumgebung(monkeypatch)
    code = _laden().main(["--nur-aktuelle"], env={}, chat=_richtig())
    ausgabe = capsys.readouterr()
    assert code == 1
    assert "übersprungen: coder (LITELLM oder LITELLM_BENCHMARK_KEY nicht gesetzt)" in ausgabe.out
    assert "übersprungen: reviewer-go (LITELLM oder LITELLM_BENCHMARK_KEY nicht gesetzt)" in (
        ausgabe.out)
    assert "keine aktiven Anbieter" in ausgabe.err


def test_beispielliste_hat_platzhalter_statt_schluessel():
    beispiel = ROOT / "sandbox" / "benchmark_providers.example.json"
    roh = json.loads(beispiel.read_text(encoding="utf-8"))
    assert all("api_key" not in e for e in roh["providers"])
    assert all(e["modell"] for e in roh["providers"] if e["art"] == "openai-kompatibel")
