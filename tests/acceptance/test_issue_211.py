"""Akzeptanztest: Ein geparktes Issue, das wieder auf `bereit` gesetzt wird, verliert Parkmarke und Zähler."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_entparken", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_entparken"] = w
_spec.loader.exec_module(w)

T0 = 1760880000.0  # feste Epochen-Sekunden
MIN = 60.0


def _iso(sekunden: float) -> str:
    return datetime.fromtimestamp(sekunden, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    monkeypatch.setattr(w, "PREFIX", "")
    projekt = w.projects.default_projects()[0]
    monkeypatch.setattr(w, "PROJECTS", [projekt])
    monkeypatch.setattr(w, "REPO", projekt.repo)
    monkeypatch.setattr(w, "PROJECT", projekt.name)


def _geparkt(skey="issue324", versuch_vor_min=60.0):
    return {
        skey: 2,
        f"_park:{skey}": "geparkt",
        f"_versuch:{skey}": T0 - versuch_vor_min * MIN,
        "_gherr": "unberuehrt",
    }


def test_versuch_zaehlen_merkt_den_zeitpunkt():
    state = {}
    assert w.versuch_zaehlen(state, "issue324", now=T0) == 1
    assert state["issue324"] == 1
    assert state["_versuch:issue324"] == T0
    assert w.versuch_zaehlen(state, "issue324", now=T0 + MIN) == 2
    assert state["issue324"] == 2
    assert state["_versuch:issue324"] == T0 + MIN


def test_bereit_nach_dem_letzten_versuch_hebt_die_parkmarke_auf(capsys):
    state = _geparkt()
    assert w.entparken(state, lambda skey: T0) == ["issue324"]
    assert state == {"_gherr": "unberuehrt"}
    assert "issue324" in capsys.readouterr().out


def test_bereit_vor_dem_letzten_versuch_bleibt_geparkt():
    state = _geparkt()
    assert w.entparken(state, lambda skey: T0 - 120 * MIN) == []
    assert state["issue324"] == 2 and "_park:issue324" in state


def test_ohne_bereit_ereignis_bleibt_geparkt():
    state = _geparkt()
    assert w.entparken(state, lambda skey: None) == []
    assert state["issue324"] == 2 and "_park:issue324" in state


def test_ohne_versuchsmarke_bleibt_geparkt():
    state = _geparkt()
    del state["_versuch:issue324"]
    assert w.entparken(state, lambda skey: T0) == []
    assert state["issue324"] == 2 and "_park:issue324" in state


def test_fehlschlagende_abfrage_laesst_die_marke_stehen():
    def kaputt(skey):
        raise RuntimeError("gh api fehlgeschlagen")

    state = _geparkt()
    assert w.entparken(state, kaputt) == []
    assert state["issue324"] == 2 and "_park:issue324" in state


def test_nur_issue_auftraege_werden_geprueft():
    gefragt = []

    def bereit_seit(skey):
        gefragt.append(skey)
        return T0

    state = {"pr12@abc": 2, "_park:pr12@abc": "geparkt", "_versuch:pr12@abc": T0 - 60 * MIN}
    assert w.entparken(state, bereit_seit) == []
    assert gefragt == []
    assert "_park:pr12@abc" in state


def test_label_bereit_seit_nimmt_das_juengste_labeled_bereit(monkeypatch):
    aufrufe = []
    ereignisse = [
        {"event": "labeled", "label": {"name": "bereit"}, "created_at": _iso(T0 - 120 * MIN)},
        {"event": "labeled", "label": {"name": "in-arbeit"}, "created_at": _iso(T0 - 60 * MIN)},
        {"event": "labeled", "label": {"name": "bereit"}, "created_at": _iso(T0 - 10 * MIN)},
        {"event": "unlabeled", "label": {"name": "bereit"}, "created_at": _iso(T0 - 5 * MIN)},
        {"event": "commented", "created_at": _iso(T0 - 1 * MIN)},
    ]

    def fake_gh(*args, repo=None):
        aufrufe.append((args, repo))
        return ereignisse

    monkeypatch.setattr(w, "gh", fake_gh)
    assert w.label_bereit_seit("issue324") == T0 - 10 * MIN
    args, _ = aufrufe[0]
    assert args[0] == "api"
    assert f"repos/{w.REPO}/issues/324/events" in args[1]


def test_label_bereit_seit_ohne_issue_auftrag_oder_ereignis(monkeypatch):
    monkeypatch.setattr(w, "gh", lambda *a, **k: [])
    assert w.label_bereit_seit("issue324") is None
    assert w.label_bereit_seit("pr12@abc") is None
    assert w.label_bereit_seit("unbekannt/issue5") is None


def test_gh_api_bekommt_kein_repo_argument(monkeypatch):
    befehle = []

    class Antwort:
        stdout = "[]"

    def fake_run(cmd, **kwargs):
        befehle.append(cmd)
        return Antwort()

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    w.gh("api", "repos/x/y/issues/1/events")
    w.gh("issue", "list")
    assert "--repo" not in befehle[0]
    assert "--repo" in befehle[1]
