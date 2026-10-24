"""Tests für den gehärteten Takt-Push (#280, Teil 3 / #319).

Der Push selbst bleibt root; die Härtung läuft ausschließlich über die Umgebung: Hook-Ordner
außerhalb von Klon und Slot-Worktrees, `core.fsmonitor` aus.
"""

import importlib.util
import os
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_push_haertung_tests", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_push_haertung_tests"] = w
_spec.loader.exec_module(w)


def test_pre_push_hooks_dir_ist_reine_pfadberechnung(monkeypatch, tmp_path):
    """Der Pfad entsteht ohne Seiteneffekt: kein Verzeichnis, keine Datei (#319)."""
    claims = tmp_path / "claims"
    monkeypatch.setattr(w, "CLAIM_DIR", str(claims))
    assert w.pre_push_hooks_dir() == os.path.join(str(claims), "pre-push-hooks")
    assert not claims.exists()


def test_pre_push_hooks_dir_folgt_claim_dir(monkeypatch, tmp_path):
    """Das Verzeichnis hängt an CLAIM_DIR, damit der Hook nicht im Klon des Slots liegt."""
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "andere-claims"))
    assert w.pre_push_hooks_dir() == os.path.join(str(tmp_path / "andere-claims"), "pre-push-hooks")


def test_push_env_haertet_hooks_und_fsmonitor(monkeypatch, tmp_path):
    """Die Umgebung erbt ENV und setzt genau die beiden git-Konfigurationen (#319)."""
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setitem(w.ENV, "PUSH_ENV_MERKMAL", "1")
    env = w.push_env()
    assert env is not w.ENV
    assert env["PUSH_ENV_MERKMAL"] == "1"
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert env["GIT_CONFIG_VALUE_0"] == str(tmp_path / "claims" / "pre-push-hooks")
    assert env["GIT_CONFIG_KEY_1"] == "core.fsmonitor"
    assert env["GIT_CONFIG_VALUE_1"] == "false"


def test_push_env_legt_kein_verzeichnis_an(monkeypatch, tmp_path):
    """push_env() darf open_pr() nicht in den echten CLAIM_DIR schreiben lassen (#319)."""
    claims = tmp_path / "claims"
    monkeypatch.setattr(w, "CLAIM_DIR", str(claims))
    w.push_env()
    assert not claims.exists()
