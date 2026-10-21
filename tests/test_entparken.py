"""Tests für das automatische Entparken und Zählen der Versuche in main()."""
import importlib.util
from pathlib import Path
import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_test_entparken", _PATH)
w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w)


def test_main_ruft_entparken_vor_auftragswahl_auf(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(w, "STATE", str(state_file))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "cleanup_claims", lambda *a, **k: 0)

    entparken_gerufen = []

    def mock_entparken(state, bereit_seit):
        entparken_gerufen.append((dict(state), bereit_seit))
        return []

    monkeypatch.setattr(w, "entparken", mock_entparken)
    monkeypatch.setattr(w, "prune_parked", lambda s, fn: [])
    monkeypatch.setattr(w, "pick_job", lambda s: None)

    w.main()
    assert len(entparken_gerufen) == 1
    assert entparken_gerufen[0][1] == w.label_bereit_seit


def test_main_zaehlt_versuch_ueber_versuch_zaehlen(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(w, "STATE", str(state_file))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "cleanup_claims", lambda *a, **k: 0)

    zaehlen_gerufen = []

    def mock_versuch_zaehlen(state, skey, now=None):
        zaehlen_gerufen.append((skey, now))
        state[skey] = state.get(skey, 0) + 1
        return state[skey]

    monkeypatch.setattr(w, "versuch_zaehlen", mock_versuch_zaehlen)
    monkeypatch.setattr(w, "prune_parked", lambda s, fn: [])
    monkeypatch.setattr(w, "entparken", lambda s, fn: [])
    monkeypatch.setattr(w, "pick_job", lambda s: ("issue42", "echo test", None, None))
    monkeypatch.setattr(w, "claim_job", lambda s: True)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "ensure_pre_push_hook", lambda: None)
    monkeypatch.setattr(w, "choose_model", lambda: ("coder", None))
    monkeypatch.setattr(w, "run_agent", lambda prompt: 0)
    monkeypatch.setattr(w, "open_pr", lambda *a, **k: True)
    monkeypatch.setattr(w, "slot_status", lambda *a, **k: None)

    w.main()
    assert len(zaehlen_gerufen) == 1
    assert zaehlen_gerufen[0][0] == "issue42"
