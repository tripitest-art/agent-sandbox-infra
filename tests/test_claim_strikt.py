"""Tests für CLAIM_STRIKT in main(): Zähler und Entparken behalten ihre Aufrufe (#311).

Der Akzeptanztest prüft die neuen Funktionen einzeln. Hier geht es um die Verdrahtung in
main(): `versuch_zaehlen` und `entparken` bekommen weiterhin dieselben Argumente, und mit
ausgeschaltetem Schalter entsteht kein zusätzlicher `gh`-Aufruf.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_claim_main", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_claim_main"] = w
_spec.loader.exec_module(w)

T0 = 1760880000.0


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    monkeypatch.setattr(w, "PREFIX", "")
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path / "work"))
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "cleanup_claims", lambda *a, **k: 0)
    monkeypatch.setattr(w, "verwaiste_melden", lambda state, now=None: [])
    monkeypatch.setattr(w, "prune_parked", lambda state, still_open: [])
    monkeypatch.setattr(w, "stillstand_pruefen", lambda *a, **k: False)
    monkeypatch.setattr(w, "slot_status", lambda *a, **k: None)
    monkeypatch.setattr(w, "jetzt_s", lambda: T0)


@pytest.mark.parametrize("strikt", [False, True])
def test_main_entparkt_mit_bisherigen_argumenten_und_ohne_gh_im_leerlauf(monkeypatch, strikt):
    monkeypatch.setattr(w, "CLAIM_STRIKT", strikt)
    state = {}
    monkeypatch.setattr(w, "load_state", lambda: state)
    monkeypatch.setattr(w, "pick_job", lambda s: None)

    entparkt = []

    def mock_entparken(*args, **kw):
        entparkt.append((args, kw))
        return []

    monkeypatch.setattr(w, "entparken", mock_entparken)
    gh_aufrufe = []
    monkeypatch.setattr(w, "gh", lambda *a, **k: gh_aufrufe.append((a, k)) or [])

    assert w.main() == 0

    assert len(entparkt) == 1
    args, kw = entparkt[0]
    assert args == (state, w.label_bereit_seit)
    assert kw == {}
    assert gh_aufrufe == []


@pytest.mark.parametrize("strikt", [False, True])
def test_main_zaehlt_mit_bisherigen_argumenten(monkeypatch, strikt):
    monkeypatch.setattr(w, "CLAIM_STRIKT", strikt)
    state = {}
    monkeypatch.setattr(w, "load_state", lambda: state)
    monkeypatch.setattr(w, "pick_job", lambda s: ("issue42", "echo test", None, None))
    monkeypatch.setattr(w, "entparken", lambda state, bereit_seit: [])
    monkeypatch.setattr(w, "claim_job", lambda skey: True)
    monkeypatch.setattr(w, "agent_identitaet", lambda: (None, None))
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "ensure_pre_push_hook", lambda: None)
    monkeypatch.setattr(w, "choose_model", lambda: ("coder", None))
    monkeypatch.setattr(w, "run_agent", lambda prompt: 0)
    monkeypatch.setattr(w, "open_pr", lambda *a, **k: True)
    monkeypatch.setattr(w, "check_issue_result", lambda key: None)

    gh_aufrufe = []

    def fake_gh(*args, **kw):
        gh_aufrufe.append((args, kw))
        return []

    monkeypatch.setattr(w, "gh", fake_gh)

    gezaehlt = []

    def mock_versuch_zaehlen(*args, **kw):
        gezaehlt.append((args, kw))
        state[args[1]] = state.get(args[1], 0) + 1
        return state[args[1]]

    monkeypatch.setattr(w, "versuch_zaehlen", mock_versuch_zaehlen)

    assert w.main() == 0

    assert len(gezaehlt) == 1
    args, kw = gezaehlt[0]
    assert args == (state, "issue42")
    assert kw == {}
    assert gh_aufrufe == [
        (("issue", "edit", "42", "--add-label", "in-arbeit", "--remove-label", "bereit"), {})
    ]
