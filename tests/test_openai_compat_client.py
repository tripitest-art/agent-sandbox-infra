"""Tests für `sandbox/core/openai_compat_client`: ein Aufruf, ein `dict`, nie eine Ausnahme.

Ohne Netzwerk: `urllib.request.urlopen` ist durch einen Fake ersetzt, der die Anfrage festhält
und eine vorgegebene Antwort liefert. Der Schlüssel steht nur im Header und darf in keinem
Ergebnis auftauchen (Regel 5).
"""

import io
import json
import urllib.error

import pytest

from sandbox.core import openai_compat_client as oc

KEY = "key-nur-im-header-0815"
ANBIETERTEXT = "stacktrace-des-anbieters-mit-" + KEY
FORM = {"ok", "text", "fehler", "ratenbegrenzt", "dauer_s", "usage", "kosten"}


class Antwort:
    """Minimaler Ersatz für die Antwort von urllib.request.urlopen."""

    def __init__(self, inhalt: bytes):
        self.inhalt = inhalt

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self.inhalt


def _urlopen(monkeypatch, antwort=None, fehler: BaseException | None = None) -> list[dict]:
    calls: list[dict] = []

    def fake(req, timeout=None):
        calls.append({"req": req, "timeout": timeout})
        if fehler is not None:
            raise fehler
        return antwort

    monkeypatch.setattr(oc.urllib.request, "urlopen", fake)
    return calls


def _antwort(daten) -> Antwort:
    return Antwort(json.dumps(daten).encode())


def _gut(text: str = "hallo", **zusatz) -> Antwort:
    daten = {"choices": [{"message": {"content": text}}]}
    daten.update(zusatz)
    return _antwort(daten)


def _body(calls: list[dict]) -> dict:
    return json.loads(calls[0]["req"].data)


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/", code, ANBIETERTEXT, {}, io.BytesIO(b""))


def _ohne_leck(r: dict) -> None:
    """Form prüfen und sicherstellen, dass weder Schlüssel noch Anbietertext im Ergebnis steht."""
    assert set(r) == FORM
    assert isinstance(r["dauer_s"], float) and r["dauer_s"] >= 0
    assert KEY not in repr(r) and ANBIETERTEXT not in repr(r)


def test_body_hat_vorgaben_und_form(monkeypatch):
    calls = _urlopen(monkeypatch, _gut())
    r = oc.chat("http://x/v1", "m", "p")
    assert _body(calls) == {
        "model": "m",
        "messages": [{"role": "user", "content": "p"}],
        "max_tokens": 2048,
        "temperature": 0.0,
        "stream": False,
    }
    assert r["ok"] is True and r["text"] == "hallo"
    assert r["fehler"] is None and r["ratenbegrenzt"] is False
    assert r["usage"] is None and r["kosten"] is None
    _ohne_leck(r)


def test_mehrere_schraegstriche_am_ende_werden_entfernt(monkeypatch):
    calls = _urlopen(monkeypatch, _gut())
    oc.chat("https://host/openai/v1///", "m", "p")
    assert calls[0]["req"].full_url == "https://host/openai/v1/chat/completions"


def test_extra_body_none_und_leeres_dict_aendern_nichts(monkeypatch):
    calls = _urlopen(monkeypatch, _gut())
    oc.chat("http://x/v1", "m", "p", extra_body=None)
    oc.chat("http://x/v1", "m", "p", extra_body={})
    assert _body([calls[0]]) == _body([calls[1]]) == {
        "model": "m",
        "messages": [{"role": "user", "content": "p"}],
        "max_tokens": 2048,
        "temperature": 0.0,
        "stream": False,
    }


def test_extra_body_ueberschreibt_max_tokens_und_temperature(monkeypatch):
    """Ergänzungen des Anrufers gewinnen — außer bei `model` und `messages`."""
    calls = _urlopen(monkeypatch, _gut())
    oc.chat("http://x/v1", "m", "p", max_tokens=10, extra_body={"max_tokens": 99})
    assert _body(calls)["max_tokens"] == 99


def test_autorisation_fehlt_ohne_key(monkeypatch):
    calls = _urlopen(monkeypatch, _gut())
    oc.chat("http://x/v1", "m", "p", api_key="")
    req = calls[0]["req"]
    assert not req.has_header("Authorization")
    # Der Schlüssel darf auch nicht als Teil einer anderen Kopfzeile auftauchen.
    assert KEY not in repr(dict(req.header_items()))


def test_kosten_bleiben_zahl_und_werden_nicht_gerundet(monkeypatch):
    _urlopen(monkeypatch, _gut(usage={"cost": 0.123456789}))
    r = oc.chat("http://x/v1", "m", "p")
    assert r["kosten"] == 0.123456789
    assert r["usage"] == {"cost": 0.123456789}


def test_nur_leerzeichen_ist_kein_leerer_text(monkeypatch):
    _urlopen(monkeypatch, _antwort({"choices": [{"message": {"content": " "}}]}))
    r = oc.chat("http://x/v1", "m", "p")
    assert r["ok"] is True and r["text"] == " " and r["fehler"] is None


def test_message_als_nicht_dict_ist_leere_antwort(monkeypatch):
    _urlopen(monkeypatch, _antwort({"choices": [{"message": "hallo"}]}))
    r = oc.chat("http://x/v1", "m", "p")
    assert r["ok"] is False and r["fehler"] == "leere Antwort"


def test_http_401_nennt_code_ohne_ratenbegrenzung(monkeypatch):
    _urlopen(monkeypatch, fehler=_http(401))
    r = oc.chat("http://x/v1", "m", "p", api_key=KEY)
    assert r["ok"] is False and r["fehler"] == "HTTP 401"
    assert r["ratenbegrenzt"] is False
    assert r["usage"] is None and r["kosten"] is None
    _ohne_leck(r)


def test_ausnahmemeldung_mit_key_gelangt_nicht_ins_ergebnis(monkeypatch):
    _urlopen(monkeypatch, fehler=OSError(ANBIETERTEXT))
    r = oc.chat("http://x/v1", "m", "p", api_key=KEY)
    assert r["fehler"] == "OSError"
    _ohne_leck(r)


def test_kaputtes_json_nennt_nur_den_typ(monkeypatch):
    _urlopen(monkeypatch, Antwort(b"kein json " + KEY.encode()))
    r = oc.chat("http://x/v1", "m", "p", api_key=KEY)
    assert r["ok"] is False and r["fehler"] == "JSONDecodeError"
    _ohne_leck(r)


@pytest.mark.parametrize("base_url", ["", "kein-schema", "/relativ"])
def test_unbrauchbare_base_url_wirft_nicht(base_url):
    r = oc.chat(base_url, "m", "p", api_key=KEY)
    assert r["ok"] is False and r["fehler"] == "ValueError"
    assert r["usage"] is None and r["kosten"] is None
    _ohne_leck(r)


def test_dauer_wird_gemessen(monkeypatch):
    """`dauer_s` folgt der Uhr von `time.monotonic`, nicht einer fertigen Zahl."""
    _urlopen(monkeypatch, _gut())
    zeiten = iter([100.0, 100.25])
    monkeypatch.setattr(oc.time, "monotonic", lambda: next(zeiten))
    r = oc.chat("http://x/v1", "m", "p")
    assert r["dauer_s"] == pytest.approx(0.25)
