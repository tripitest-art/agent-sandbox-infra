import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sandbox.core import benchmark_code, benchmark_providers, benchmark_review

ROOT = Path(__file__).resolve().parents[2]
_PFAD = ROOT / "sandbox" / "benchmark.py"
SCHLUESSEL = "geheim-123456"
ZEILEN_SCHLUESSEL = {
    "lauf", "zeit", "anbieter", "rolle", "aufgabe", "art", "versuch",
    "bestanden", "grund", "ratenbegrenzt", "dauer_s", "kosten",
}


def _laden():
    spec = importlib.util.spec_from_file_location("benchmark_issue_test", _PFAD)
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
    """Merkt sich die Aufrufe; `antwort(aufruf)` liefert das chat-Ergebnis."""

    def __init__(self, antwort=None):
        self.aufrufe = []
        richtig = _richtige_antworten()
        self.antwort = antwort or (lambda a: _ok(richtig[a["prompt"]]))

    def __call__(self, base_url, model, prompt, api_key="", timeout=120.0,
                 max_tokens=2048, temperature=0.0, extra_body=None):
        aufruf = {"base_url": base_url, "model": model, "prompt": prompt, "api_key": api_key,
                  "timeout": timeout, "extra_body": extra_body}
        self.aufrufe.append(aufruf)
        return self.antwort(aufruf)


def _provider(name="p1", key=SCHLUESSEL, rolle=""):
    return benchmark_providers.Provider(
        name=name, art="openai-kompatibel", base_url=f"https://{name}.example/v1",
        modell=f"{name}-modell", api_key=key, extra_body={"x": 1}, rolle=rolle,
    )


def _lauf(providers, chat, code=None, review=None, **kw):
    b = _laden()
    kw.setdefault("schlafen", lambda s: None)
    kw.setdefault("jetzt", lambda: "2026-01-01T00:00:00Z")
    return b.run_benchmark(
        providers,
        benchmark_code.CODE_TASKS[:1] if code is None else code,
        benchmark_review.REVIEW_TASKS[:1] if review is None else review,
        chat, **kw,
    )


def test_reihenfolge_zeilen_und_aufrufargumente():
    chat = FakeChat()
    p = _provider(rolle="worker")
    zeilen, abgebrochen = _lauf([p], chat, wiederholungen=2, aufruf_timeout_s=33.0, lauf_id="L1")
    assert abgebrochen is False
    assert [(z["art"], z["versuch"]) for z in zeilen] == [
        ("code", 1), ("code", 2), ("review", 1), ("review", 2)]
    for z in zeilen:
        assert set(z) == ZEILEN_SCHLUESSEL
        assert z["bestanden"] is True and z["grund"] is None
        assert z["anbieter"] == "p1" and z["rolle"] == "worker" and z["lauf"] == "L1"
        assert z["zeit"] == "2026-01-01T00:00:00Z" and z["ratenbegrenzt"] is False
        assert z["dauer_s"] == 0.5 and z["kosten"] is None
    assert zeilen[0]["aufgabe"] == benchmark_code.CODE_TASKS[0]["id"]
    assert zeilen[2]["aufgabe"] == benchmark_review.REVIEW_TASKS[0]["id"]
    erster = chat.aufrufe[0]
    assert erster["base_url"] == p.base_url and erster["model"] == p.modell
    assert erster["api_key"] == SCHLUESSEL and erster["timeout"] == 33.0
    assert erster["extra_body"] == {"x": 1}
    assert erster["prompt"] == benchmark_code.build_code_prompt(benchmark_code.CODE_TASKS[0])
    assert len(chat.aufrufe) == 4


def test_lauf_id_leer_nimmt_die_startzeit():
    zeilen, _ = _lauf([_provider()], FakeChat())
    assert {z["lauf"] for z in zeilen} == {"2026-01-01T00:00:00Z"}


def test_anbieter_laufen_nacheinander():
    zeilen, _ = _lauf([_provider("p1"), _provider("p2")], FakeChat(), wiederholungen=1)
    assert [z["anbieter"] for z in zeilen] == ["p1", "p1", "p2", "p2"]


def test_falsche_antworten_sind_nicht_bestanden_mit_grund():
    antworten = {
        benchmark_code.build_code_prompt(benchmark_code.CODE_TASKS[0]): "Ich weiß es nicht.",
        benchmark_review.build_review_prompt(benchmark_review.REVIEW_TASKS[0]): "Freigegeben\nAlles gut.",
    }
    zeilen, _ = _lauf([_provider()], FakeChat(lambda a: _ok(antworten[a["prompt"]])), wiederholungen=1)
    assert [(z["bestanden"], z["grund"]) for z in zeilen] == [
        (False, "kein Code"), (False, "falsches Urteil")]


def test_fehler_ohne_wiederholung_und_kosten_werden_durchgereicht():
    chat = FakeChat(lambda a: _fehler("HTTP 500"))
    zeilen, _ = _lauf([_provider()], chat, wiederholungen=1)
    assert len(chat.aufrufe) == 2
    assert [(z["bestanden"], z["grund"], z["ratenbegrenzt"]) for z in zeilen] == [
        (False, "HTTP 500", False)] * 2
    richtig = _richtige_antworten()
    zeilen, _ = _lauf([_provider()], FakeChat(lambda a: _ok(richtig[a["prompt"]], kosten=0.002)),
                      wiederholungen=1)
    assert {z["kosten"] for z in zeilen} == {0.002}


def test_429_einmal_wiederholen_dann_ratenbegrenzt():
    richtig = _richtige_antworten()
    folge = iter([_fehler("HTTP 429", rate=True)])
    schlaf = []
    chat = FakeChat(lambda a: next(folge, None) or _ok(richtig[a["prompt"]]))
    zeilen, _ = _lauf([_provider()], chat, code=benchmark_code.CODE_TASKS[:1], review=[],
                      wiederholungen=1, pause_429_s=7.0, schlafen=schlaf.append)
    assert len(chat.aufrufe) == 2 and schlaf == [7.0]
    assert zeilen[0]["bestanden"] is True and zeilen[0]["ratenbegrenzt"] is False

    schlaf.clear()
    chat = FakeChat(lambda a: _fehler("HTTP 429", rate=True))
    zeilen, _ = _lauf([_provider()], chat, wiederholungen=1, pause_429_s=7.0, schlafen=schlaf.append)
    assert len(chat.aufrufe) == 4 and schlaf == [7.0, 7.0]
    assert [(z["bestanden"], z["grund"], z["ratenbegrenzt"]) for z in zeilen] == [
        (False, "ratenbegrenzt", True)] * 2


def test_gesamtlimit_bricht_ab_und_behaelt_zeilen():
    stand = {"t": 0.0}

    def uhr():
        t = stand["t"]
        stand["t"] += 10.0
        return t

    chat = FakeChat()
    zeilen, abgebrochen = _lauf([_provider()], chat, wiederholungen=5, gesamt_limit_s=25.0, uhr=uhr)
    assert abgebrochen is True and len(zeilen) == 2 and len(chat.aufrufe) == 2


def test_wiederholungen_unter_eins_ist_ein_fehler():
    with pytest.raises(ValueError):
        _lauf([_provider()], FakeChat(), wiederholungen=0)


def test_schluessel_steht_nie_in_den_zeilen():
    zeilen, _ = _lauf([_provider()], FakeChat(), wiederholungen=1)
    assert SCHLUESSEL not in json.dumps(zeilen, ensure_ascii=False)


def test_zusammenfassung_zaehlt_ratenbegrenzte_nicht_als_gewertet():
    b = _laden()

    def z(anbieter, art, ok, rate=False):
        return {"anbieter": anbieter, "art": art, "bestanden": ok, "ratenbegrenzt": rate}

    ergebnis = b.zusammenfassung([
        z("p1", "code", True), z("p1", "code", True), z("p1", "code", False),
        z("p1", "code", False, rate=True), z("p2", "review", False, rate=True),
    ])
    assert set(ergebnis) == {"p1", "p2"} and set(ergebnis["p1"]) == {"code"}
    assert ergebnis["p1"]["code"]["bestanden"] == 2 and ergebnis["p1"]["code"]["gewertet"] == 3
    assert ergebnis["p1"]["code"]["ratenbegrenzt"] == 1
    assert ergebnis["p1"]["code"]["quote"] == pytest.approx(2 / 3)
    assert ergebnis["p2"]["review"] == {"bestanden": 0, "gewertet": 0, "ratenbegrenzt": 1, "quote": None}


def test_append_ergebnisse_haengt_zeilen_an(tmp_path):
    b = _laden()
    pfad = tmp_path / "e.jsonl"
    b.append_ergebnisse(str(pfad), [])
    assert not pfad.exists()
    b.append_ergebnisse(str(pfad), [{"a": 1, "g": "Änderungen nötig"}])
    b.append_ergebnisse(str(pfad), [{"a": 2}, {"a": 3}])
    text = pfad.read_text(encoding="utf-8")
    assert "Änderungen nötig" in text
    assert [json.loads(x)["a"] for x in text.splitlines()] == [1, 2, 3]
    with pytest.raises(OSError):
        b.append_ergebnisse(str(tmp_path / "fehlt" / "e.jsonl"), [{"a": 1}])


def _liste(tmp_path, eintraege):
    pfad = tmp_path / "anbieter.json"
    pfad.write_text(json.dumps({"providers": eintraege}), encoding="utf-8")
    return str(pfad)


def _eintrag(name="p1", env="P1_KEY"):
    return {"name": name, "art": "openai-kompatibel", "base_url": f"https://{name}.example/v1",
            "modell": f"{name}-modell", "api_key_umgebung": env}


def _modellumgebung(monkeypatch):
    for n in ("LITELLM_MODEL", "REVIEW_MODEL", "REVIEW_MODEL_BIG"):
        monkeypatch.delenv(n, raising=False)


def test_main_gesamtlauf_ohne_schluessel_in_ausgabe(tmp_path, capsys, monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    out = tmp_path / "e.jsonl"
    code = b.main(
        ["--providers", _liste(tmp_path, [_eintrag()]), "--out", str(out),
         "--wiederholungen", "1", "--ohne-aktuelle"],
        env={"P1_KEY": SCHLUESSEL}, chat=FakeChat(), schlafen=lambda s: None,
    )
    ausgabe = capsys.readouterr()
    assert code == 0
    zeilen = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(zeilen) == len(benchmark_code.CODE_TASKS) + len(benchmark_review.REVIEW_TASKS)
    assert all(z["anbieter"] == "p1" and z["bestanden"] for z in zeilen)
    n_code, n_review = len(benchmark_code.CODE_TASKS), len(benchmark_review.REVIEW_TASKS)
    assert f"p1: code {n_code}/{n_code} review {n_review}/{n_review} ratenbegrenzt 0" in ausgabe.out
    assert f"Ergebnisse: {out} ({len(zeilen)} Zeilen)" in ausgabe.out
    assert SCHLUESSEL not in ausgabe.out + ausgabe.err + out.read_text(encoding="utf-8")


def test_main_ohne_schluessel_wird_uebersprungen_und_ist_exit_1(tmp_path, capsys, monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    out = tmp_path / "e.jsonl"
    code = b.main(["--providers", _liste(tmp_path, [_eintrag()]), "--out", str(out), "--ohne-aktuelle"],
                  env={}, chat=FakeChat())
    ausgabe = capsys.readouterr()
    assert code == 1 and not out.exists()
    assert "übersprungen: p1 (P1_KEY nicht gesetzt)" in ausgabe.out
    assert "keine aktiven Anbieter" in ausgabe.err


def test_main_kaputte_liste_bricht_mit_2_ab(tmp_path, capsys, monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    kaputt = tmp_path / "kaputt.json"
    kaputt.write_text("{kein json", encoding="utf-8")
    out = tmp_path / "e.jsonl"
    code = b.main(["--providers", str(kaputt), "--out", str(out)], env={}, chat=FakeChat())
    assert code == 2 and not out.exists()
    assert "Providerliste ungültig" in capsys.readouterr().err


def test_main_nur_filtert_und_kennt_nur_vorhandene_namen(tmp_path, capsys, monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    liste = _liste(tmp_path, [_eintrag("p1", "P1_KEY"), _eintrag("p2", "P2_KEY")])
    env = {"P1_KEY": "a", "P2_KEY": "b"}
    out = tmp_path / "e.jsonl"
    code = b.main(["--providers", liste, "--out", str(out), "--wiederholungen", "1",
                   "--ohne-aktuelle", "--nur", "p2"], env=env, chat=FakeChat(),
                  schlafen=lambda s: None)
    assert code == 0
    assert {json.loads(x)["anbieter"] for x in out.read_text(encoding="utf-8").splitlines()} == {"p2"}
    capsys.readouterr()
    code = b.main(["--providers", liste, "--out", str(tmp_path / "x.jsonl"), "--ohne-aktuelle",
                   "--nur", "gibt-es-nicht"], env=env, chat=FakeChat())
    assert code == 2 and "unbekannter Anbieter: gibt-es-nicht" in capsys.readouterr().err


def test_main_nimmt_aktuelle_aliase_mit(tmp_path, monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    chat = FakeChat()
    out = tmp_path / "e.jsonl"
    code = b.main(
        ["--providers", _liste(tmp_path, []), "--out", str(out), "--wiederholungen", "1"],
        env={"LITELLM": "http://llm.example:4000", "LITELLM_BENCHMARK_KEY": SCHLUESSEL},
        chat=chat, schlafen=lambda s: None,
    )
    assert code == 0
    zeilen = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    rollen = {z["anbieter"]: z["rolle"] for z in zeilen}
    assert rollen == {"coder": "worker", "reviewer-go": "review+review_gross"}
    assert {a["model"] for a in chat.aufrufe} == {"coder", "reviewer-go"}
    assert {a["extra_body"]["disable_fallbacks"] for a in chat.aufrufe} == {True}


def test_main_nur_aktuelle_liest_keine_liste(tmp_path, monkeypatch):
    _modellumgebung(monkeypatch)
    b = _laden()
    out = tmp_path / "e.jsonl"
    code = b.main(["--nur-aktuelle", "--out", str(out), "--wiederholungen", "1"],
                  env={"LITELLM": "http://llm.example:4000", "LITELLM_BENCHMARK_KEY": SCHLUESSEL},
                  chat=FakeChat(), schlafen=lambda s: None)
    assert code == 0 and out.exists()
    with pytest.raises(SystemExit) as e:
        b.main(["--nur-aktuelle", "--providers", str(tmp_path / "x.json")], env={}, chat=FakeChat())
    assert e.value.code == 2


def test_beispielliste_ist_gueltig_und_ohne_schluessel():
    beispiel = ROOT / "sandbox" / "benchmark_providers.example.json"
    aktiv, uebersprungen = benchmark_providers.load_providers(str(beispiel), {})
    assert aktiv == [] and len(uebersprungen) >= 2
    roh = json.loads(beispiel.read_text(encoding="utf-8"))
    assert {e["art"] for e in roh["providers"]} == {"litellm-alias", "openai-kompatibel"}
    assert all("api_key" not in e for e in roh["providers"])


def test_readme_nennt_benchmark_dateien_und_umgebung():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for begriff in ("benchmark.py", "benchmark_providers.example.json", "LITELLM_BENCHMARK_KEY",
                    "AGENT_BENCHMARK_PROVIDERS", "BENCHMARK_ERGEBNISSE"):
        assert begriff in readme
