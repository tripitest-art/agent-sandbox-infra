import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
VERMERK = "Referenzumsetzung: Akzeptanztests grün"
BODY = "### Betroffene Dateien\n\n- `sandbox/dashboard.py`\n"


def _laden(monkeypatch, name="worker_tick_claim_strikt", umgebung=None):
    monkeypatch.delenv("CLAIM_STRIKT", raising=False)
    if umgebung is not None:
        monkeypatch.setenv("CLAIM_STRIKT", umgebung)
    spec = importlib.util.spec_from_file_location(name, _PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _strikt(monkeypatch):
    w = _laden(monkeypatch)
    monkeypatch.setattr(w, "CLAIM_STRIKT", True)
    return w


def test_schalter_ist_vorgabe_aus_und_mit_eins_an(monkeypatch):
    assert _laden(monkeypatch).CLAIM_STRIKT is False
    assert _laden(monkeypatch, umgebung="1").CLAIM_STRIKT is True
    assert _laden(monkeypatch, umgebung="0").CLAIM_STRIKT is False


def test_text_hash(monkeypatch):
    w = _laden(monkeypatch)
    h = w.text_hash("Ziel\nTest\n")
    assert isinstance(h, str) and len(h) == 16 and int(h, 16) >= 0
    assert w.text_hash("  Ziel\r\nTest \r\n\r\n") == h          # Zeilenenden und Rand egal
    assert w.text_hash("Ziel\nTest 2\n") != h
    for leer in (None, "", "   \n ", 5, b"x"):
        assert w.text_hash(leer) is None


@pytest.mark.parametrize("body, erwartet", [
    (f"Kontext\n{VERMERK}\n", True),
    (f"- {VERMERK} (CT 106)\n", True),
    (f"> {VERMERK}", True),
    ("  * REFERENZUMSETZUNG: AKZEPTANZTESTS GRÜN", True),
    ("Referenzumsetzung: Akzeptanztests gruen", True),
    ("Die Referenzumsetzung: Akzeptanztests grün steht aus", False),
    ("Referenzumsetzung: Akzeptanztests rot", False),
    ("", False),
    (None, False),
    (7, False),
])
def test_hat_referenz_vermerk(monkeypatch, body, erwartet):
    assert _laden(monkeypatch).hat_referenz_vermerk(body) is erwartet


def test_versuch_zaehlen_merkt_den_text_nur_im_strikten_betrieb(monkeypatch):
    w = _strikt(monkeypatch)
    monkeypatch.setattr(w, "issue_body_text", lambda skey: "Text A")
    state = {}
    assert w.versuch_zaehlen(state, "issue227", now=1000.0) == 1
    assert state["_text:issue227"] == w.text_hash("Text A")
    assert state["_versuch:issue227"] == 1000.0
    monkeypatch.setattr(w, "CLAIM_STRIKT", False)
    state = {}
    w.versuch_zaehlen(state, "issue227", now=1000.0)
    assert "_text:issue227" not in state


def test_versuch_zaehlen_zaehlt_auch_wenn_die_abfrage_wirft(monkeypatch):
    w = _strikt(monkeypatch)

    def kaputt(skey):
        raise RuntimeError("gh nicht erreichbar")

    monkeypatch.setattr(w, "issue_body_text", kaputt)
    state = {}
    assert w.versuch_zaehlen(state, "issue227", now=5.0) == 1
    assert state["issue227"] == 1 and "_text:issue227" not in state


def _geparkt(w, text="Text A"):
    return {"_park:issue227": "geparkt", "issue227": 2, "_versuch:issue227": 1000.0,
            "_text:issue227": w.text_hash(text)}


def test_entparken_laesst_den_auftrag_bei_gleichem_text_geparkt(monkeypatch, capsys):
    w = _strikt(monkeypatch)
    monkeypatch.setattr(w, "issue_body_text", lambda skey: "Text A\r\n")
    state = _geparkt(w)
    assert w.entparken(state, lambda skey: 2000.0) == []
    assert "_park:issue227" in state and state["issue227"] == 2
    assert w.entparken(state, lambda skey: 2000.0) == []            # zweiter Takt, derselbe Zeitpunkt
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "Issue-Text ist seit dem letzten Versuch unverändert" in z]
    assert len(zeilen) == 1
    assert zeilen[0].endswith("issue227: bereit gesetzt, aber der Issue-Text ist seit dem letzten Versuch unverändert, bleibt geparkt.")
    assert w.entparken(state, lambda skey: 3000.0) == []            # neues bereit, immer noch gleicher Text
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "unverändert, bleibt geparkt" in z]
    assert len(zeilen) == 1


def test_entparken_loest_bei_geaendertem_text(monkeypatch):
    w = _strikt(monkeypatch)
    monkeypatch.setattr(w, "issue_body_text", lambda skey: "Text B")
    state = _geparkt(w)
    assert w.entparken(state, lambda skey: 2000.0) == ["issue227"]
    for rest in ("_park:issue227", "issue227", "_versuch:issue227", "_text:issue227", "_text_gleich:issue227"):
        assert rest not in state


def test_entparken_braucht_ein_neueres_bereit_auch_bei_geaendertem_text(monkeypatch):
    w = _strikt(monkeypatch)
    monkeypatch.setattr(w, "issue_body_text", lambda skey: "Text B")
    state = _geparkt(w)
    assert w.entparken(state, lambda skey: 500.0) == []
    assert w.entparken(state, lambda skey: None) == []
    assert "_park:issue227" in state


@pytest.mark.parametrize("lookup", ["none", "wirft"])
def test_entparken_bleibt_bei_nicht_abfragbarem_text(monkeypatch, lookup):
    w = _strikt(monkeypatch)

    def abfrage(skey):
        if lookup == "wirft":
            raise RuntimeError("gh nicht erreichbar")
        return None

    monkeypatch.setattr(w, "issue_body_text", abfrage)
    state = _geparkt(w)
    assert w.entparken(state, lambda skey: 2000.0) == []
    assert "_park:issue227" in state


def test_entparken_ohne_gemerkten_hash_oder_ohne_schalter_wie_bisher(monkeypatch):
    w = _strikt(monkeypatch)
    monkeypatch.setattr(w, "issue_body_text", lambda skey: "Text A")
    state = _geparkt(w)
    del state["_text:issue227"]                                     # Zustand aus der Zeit davor
    assert w.entparken(state, lambda skey: 2000.0) == ["issue227"]
    monkeypatch.setattr(w, "CLAIM_STRIKT", False)
    state = _geparkt(w)                                             # gleicher Text, aber Schalter aus
    assert w.entparken(state, lambda skey: 2000.0) == ["issue227"]


class _GH:
    def __init__(self, kandidaten):
        self.kandidaten = kandidaten

    def __call__(self, *args, **kw):
        if args[:2] == ("issue", "list"):
            if "in-arbeit" in args or "blockiert" in args:
                return []
            return self.kandidaten
        if args and args[0] == "api":
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _kandidat(w, nummer, body):
    return {"number": nummer, "title": f"Aufgabe {nummer}", "body": body,
            "author": {"login": w.OWNER}, "labels": [{"name": "worker-task"}, {"name": "bereit"}]}


def test_issue_job_ueberspringt_ohne_vermerk_und_nimmt_mit_vermerk(monkeypatch):
    w = _strikt(monkeypatch)
    ohne, mit = _kandidat(w, 11, BODY), _kandidat(w, 12, BODY + f"\n{VERMERK}\n")
    monkeypatch.setattr(w, "gh", _GH([ohne, mit]))
    job = w.issue_job(state={})
    assert job is not None and job[0] == "issue12"
    assert any(s.startswith("#11:") and "Vermerk" in s for s in w.SKIPPED)
    monkeypatch.setattr(w, "gh", _GH([ohne]))
    assert w.issue_job(state={}) is None
    assert any(s.startswith("#11:") for s in w.SKIPPED)


def test_issue_job_ohne_schalter_nimmt_auch_ohne_vermerk(monkeypatch):
    w = _laden(monkeypatch)
    monkeypatch.setattr(w, "gh", _GH([_kandidat(w, 11, BODY)]))
    job = w.issue_job(state={})
    assert job is not None and job[0] == "issue11"
