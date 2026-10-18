import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_audit_modell", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_audit_modell"] = w
_spec.loader.exec_module(w)


def _zeilen(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    # use_model() ändert diese Globalen; nach jedem Test zurück auf die Vorgabe.
    monkeypatch.setattr(w, "ACTIVE_MODEL", w.ACTIVE_MODEL)
    monkeypatch.setattr(w, "WORKER", w.WORKER)


def test_audit_nennt_mit_litellm_das_aktive_modell(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "MODEL", "qwen3-coder-64k")
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder-free")
    w.audit("x")
    z = _zeilen(Path(w.AUDIT_LOG))[0]
    assert z["active_model"] == "coder-free"
    assert z["model"] == "qwen3-coder-64k"          # Feld aus #85 bleibt unverändert


def test_audit_nennt_mit_ollama_das_ollama_modell(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "BACKEND", "ollama")
    monkeypatch.setattr(w, "MODEL", "test-modell")
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")  # gilt nur für litellm
    w.audit("x")
    z = _zeilen(Path(w.AUDIT_LOG))[0]
    assert z["active_model"] == "test-modell" and z["model"] == "test-modell"


def test_audit_folgt_dem_ausweichen_auf_ein_anderes_modell(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    w.audit("vorher")
    w.use_model("coder-or")
    w.audit("nachher")
    vorher, nachher = _zeilen(Path(w.AUDIT_LOG))
    assert vorher["active_model"] == w.LITELLM_MODEL
    assert nachher["active_model"] == "coder-or"


def test_issue_claimed_enthaelt_das_aktive_modell(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder-glm")
    monkeypatch.setattr(w, "gh", lambda *a, **k: "")
    assert w.claim_issue(12) is True
    z = _zeilen(Path(w.AUDIT_LOG))
    assert len(z) == 1 and z[0]["event"] == "issue_claimed" and z[0]["issue"] == 12
    assert z[0]["active_model"] == "coder-glm"


def test_alle_felder_aus_85_bleiben(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    w.audit("ereignis", issue=7)
    z = _zeilen(Path(w.AUDIT_LOG))[0]
    for feld in ("ts", "event", "slot", "model", "issue", "active_model"):
        assert feld in z
    assert z["event"] == "ereignis" and z["slot"] == w.WORKER_SLOT


def test_ein_feld_active_model_in_den_felder_ueberschreibt_nichts(tmp_path, monkeypatch):
    # Wer audit("x", active_model=...) aufruft, bekommt seinen Wert: die Aufrufer-Felder gelten zuletzt.
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
    w.audit("x", active_model="vom-aufrufer")
    assert _zeilen(Path(w.AUDIT_LOG))[0]["active_model"] == "vom-aufrufer"