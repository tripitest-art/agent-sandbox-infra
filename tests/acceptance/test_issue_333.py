import io
import json
import urllib.error

import pytest

from sandbox.core import openai_compat_client as oc

SCHLUESSEL = {"ok", "text", "fehler", "ratenbegrenzt", "dauer_s", "usage", "kosten"}
KEY = "geheimer-testschluessel-4711"
FEHLERTEXT = "interner-fehlertext-des-anbieters"


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


def _antwort(daten) -> Antwort:
    return Antwort(json.dumps(daten).encode())


def _gut(text="hallo", **zusatz) -> Antwort:
    daten = {"choices": [{"message": {"content": text}}]}
    daten.update(zusatz)
    return _antwort(daten)


def _urlopen(monkeypatch, antwort=None, fehler=None) -> list:
    calls: list = []

    def fake(req, timeout=None):
        calls.append({"req": req, "timeout": timeout})
        if fehler is not None:
            raise fehler
        return antwort

    monkeypatch.setattr("urllib.request.urlopen", fake)
    return calls


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/", code, FEHLERTEXT, {}, io.BytesIO(FEHLERTEXT.encode()))


def _body(calls) -> dict:
    return json.loads(calls[0]["req"].data)


def _pruefe_form(r: dict) -> None:
    assert set(r) == SCHLUESSEL
    assert isinstance(r["ok"], bool) and isinstance(r["ratenbegrenzt"], bool)
    assert isinstance(r["text"], str)
    assert isinstance(r["dauer_s"], float) and r["dauer_s"] >= 0
    assert KEY not in repr(r) and FEHLERTEXT not in repr(r)


def test_url_mit_und_ohne_abschliessenden_schraegstrich(monkeypatch):
    calls = _urlopen(monkeypatch, _gut())
    oc.chat("https://api.example/openai/v1", "m", "p")
    oc.chat("https://api.example/openai/v1/", "m", "p")
    assert [c["req"].full_url for c in calls] == ["https://api.example/openai/v1/chat/completions"] * 2
    assert all(c["req"].get_method() == "POST" for c in calls)


def test_body_enthaelt_die_vorgaben(monkeypatch):
    calls = _urlopen(monkeypatch, _gut())
    oc.chat("http://x/v1", "mein-modell", "Frage?", max_tokens=77, temperature=0.3)
    assert _body(calls) == {
        "model": "mein-modell",
        "messages": [{"role": "user", "content": "Frage?"}],
        "max_tokens": 77,
        "temperature": 0.3,
        "stream": False,
    }
    assert calls[0]["req"].get_header("Content-type") == "application/json"


def test_extra_body_wird_gemischt_ueberschreibt_aber_model_und_messages_nie(monkeypatch):
    calls = _urlopen(monkeypatch, _gut())
    oc.chat("http://x/v1", "echt", "echte Frage",
            extra_body={"top_p": 0.5, "disable_fallbacks": True, "model": "falsch",
                        "messages": [{"role": "user", "content": "falsch"}]})
    body = _body(calls)
    assert body["top_p"] == 0.5 and body["disable_fallbacks"] is True
    assert body["model"] == "echt"
    assert body["messages"] == [{"role": "user", "content": "echte Frage"}]


def test_authorization_nur_mit_key(monkeypatch):
    calls = _urlopen(monkeypatch, _gut())
    oc.chat("http://x/v1", "m", "p")
    oc.chat("http://x/v1", "m", "p", api_key=KEY)
    assert not calls[0]["req"].has_header("Authorization")
    assert calls[1]["req"].get_header("Authorization") == f"Bearer {KEY}"


def test_timeout_wird_durchgereicht(monkeypatch):
    calls = _urlopen(monkeypatch, _gut())
    oc.chat("http://x/v1", "m", "p", timeout=7.5)
    oc.chat("http://x/v1", "m", "p")
    assert calls[0]["timeout"] == 7.5
    assert calls[1]["timeout"] == 120.0


def test_erfolg(monkeypatch):
    _urlopen(monkeypatch, _gut("Antwort 42", usage={"total_tokens": 9, "cost": 0.0021}))
    r = oc.chat("http://x/v1", "m", "p", api_key=KEY)
    _pruefe_form(r)
    assert r["ok"] is True and r["text"] == "Antwort 42"
    assert r["fehler"] is None and r["ratenbegrenzt"] is False
    assert r["usage"] == {"total_tokens": 9, "cost": 0.0021}
    assert r["kosten"] == 0.0021


@pytest.mark.parametrize("usage, kosten", [
    ({"cost": 3}, 3),
    ({"cost": 0.5}, 0.5),
    ({"cost": True}, None),
    ({"cost": "0.5"}, None),
    ({"total_tokens": 4}, None),
])
def test_kosten_nur_aus_zahl(monkeypatch, usage, kosten):
    _urlopen(monkeypatch, _gut(usage=usage))
    r = oc.chat("http://x/v1", "m", "p")
    _pruefe_form(r)
    assert r["ok"] is True and r["usage"] == usage
    assert r["kosten"] == kosten and type(r["kosten"]) is type(kosten)


@pytest.mark.parametrize("usage", [None, "viel", [1, 2]])
def test_usage_ohne_dict_ist_none(monkeypatch, usage):
    _urlopen(monkeypatch, _gut(usage=usage))
    r = oc.chat("http://x/v1", "m", "p")
    _pruefe_form(r)
    assert r["ok"] is True and r["usage"] is None and r["kosten"] is None


@pytest.mark.parametrize("daten", [
    {"choices": []},
    {"choices": [{"message": {"content": ""}}]},
    {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": 5}}]},
    {"choices": [{"message": {}}]},
    {"choices": [{}]},
    {"choices": "x"},
    {},
    [],
    "text",
])
def test_leere_oder_unbrauchbare_antwort(monkeypatch, daten):
    _urlopen(monkeypatch, _antwort(daten))
    r = oc.chat("http://x/v1", "m", "p", api_key=KEY)
    _pruefe_form(r)
    assert r["ok"] is False and r["text"] == ""
    assert r["fehler"] == "leere Antwort" and r["ratenbegrenzt"] is False


def test_http_fehler_nennt_nur_den_code(monkeypatch):
    _urlopen(monkeypatch, fehler=_http(500))
    r = oc.chat("http://x/v1", "m", "p", api_key=KEY)
    _pruefe_form(r)
    assert r["ok"] is False and r["text"] == ""
    assert r["fehler"] == "HTTP 500" and r["ratenbegrenzt"] is False
    assert r["usage"] is None and r["kosten"] is None


def test_http_429_ist_ratenbegrenzt(monkeypatch):
    _urlopen(monkeypatch, fehler=_http(429))
    r = oc.chat("http://x/v1", "m", "p", api_key=KEY)
    _pruefe_form(r)
    assert r["ok"] is False and r["fehler"] == "HTTP 429" and r["ratenbegrenzt"] is True


@pytest.mark.parametrize("ausnahme, name", [
    (urllib.error.URLError("verbindung " + KEY), "URLError"),
    (TimeoutError("zu langsam " + KEY), "TimeoutError"),
    (ConnectionResetError(KEY), "ConnectionResetError"),
    (RuntimeError("boom " + KEY), "RuntimeError"),
])
def test_andere_ausnahmen_nur_klassenname(monkeypatch, ausnahme, name):
    _urlopen(monkeypatch, fehler=ausnahme)
    r = oc.chat("http://x/v1", "m", "p", api_key=KEY)
    _pruefe_form(r)
    assert r["ok"] is False and r["fehler"] == name and r["ratenbegrenzt"] is False


def test_kaputtes_json(monkeypatch):
    _urlopen(monkeypatch, Antwort(b"das ist kein json " + KEY.encode()))
    r = oc.chat("http://x/v1", "m", "p", api_key=KEY)
    _pruefe_form(r)
    assert r["ok"] is False and r["fehler"] == "JSONDecodeError"


def test_unbrauchbares_base_url_wirft_nicht():
    # Kein Fake: Eine Anfrage an „/chat/completions“ scheitert schon beim Bauen, ohne Netzwerk.
    r = oc.chat("", "m", "p", api_key=KEY)
    _pruefe_form(r)
    assert r["ok"] is False and r["fehler"] == "ValueError"
