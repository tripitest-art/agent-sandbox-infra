import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

_PATH = _SANDBOX / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_audit", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_audit"] = r
_spec.loader.exec_module(r)


def _zeilen(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def test_review_audit_schreibt_json_zeile(tmp_path, monkeypatch):
    f = tmp_path / "audit.jsonl"
    monkeypatch.setattr(r, "AUDIT_LOG", str(f))
    monkeypatch.setattr(r, "REPO", "tripitest-art/sc-digger")
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    r.audit("review_started", pr=7, sha="abcdef")
    z = _zeilen(f)[0]
    assert z["event"] == "review_started"
    assert z["pr"] == 7
    assert z["sha"] == "abcdef"
    assert z["model"] == "coder-glm"
    assert "ts" in z


def test_review_audit_maskiert_geheimnisse(tmp_path, monkeypatch):
    f = tmp_path / "audit.jsonl"
    monkeypatch.setattr(r, "AUDIT_LOG", str(f))
    r.audit("x", token="sk-geheim", api_key="secret", normal=123)
    z = _zeilen(f)[0]
    assert z["token"] == "***" and z["api_key"] == "***" and z["normal"] == 123
    assert "sk-geheim" not in f.read_text(encoding="utf-8")


def test_review_audit_wirft_nie(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(r, "AUDIT_LOG", str(tmp_path / "gibt" / "es" / "nicht" / "audit.jsonl"))
    r.audit("x", daten="test")
    assert "Audit-Log nicht schreibbar:" in capsys.readouterr().out
