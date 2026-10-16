import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_issue_audit", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_issue_audit"] = w
_spec.loader.exec_module(w)


def _zeilen(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def test_audit_schreibt_json_zeile(tmp_path, monkeypatch):
    f = tmp_path / "audit.jsonl"
    monkeypatch.setattr(w, "AUDIT_LOG", str(f))
    w.audit("test_ereignis", issue=7, pfad=Path("x"))
    w.audit("zweites")
    a, b = _zeilen(f)
    assert a["event"] == "test_ereignis" and a["issue"] == 7 and a["pfad"] == "x"
    assert a["slot"] == w.WORKER_SLOT and a["model"] == w.MODEL
    assert datetime.fromisoformat(a["ts"]).utcoffset() is not None
    assert b["event"] == "zweites"


def test_audit_maskiert_geheimnisse(tmp_path, monkeypatch):
    f = tmp_path / "audit.jsonl"
    monkeypatch.setattr(w, "AUDIT_LOG", str(f))
    w.audit("x", api_key="abc123", token="t", input_tokens=5)
    z = _zeilen(f)[0]
    assert z["api_key"] == "***" and z["token"] == "***" and z["input_tokens"] == 5
    assert "abc123" not in f.read_text(encoding="utf-8")


def test_audit_wirft_nie(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "gibt" / "es" / "nicht" / "a.jsonl"))
    w.audit("x", geheim_im_wert="NICHT-LOGGEN")
    out = capsys.readouterr().out
    assert "Audit-Log nicht schreibbar:" in out
    assert "NICHT-LOGGEN" not in out


def test_claim_issue_schreibt_ereignis(tmp_path, monkeypatch):
    f = tmp_path / "audit.jsonl"
    monkeypatch.setattr(w, "AUDIT_LOG", str(f))
    monkeypatch.setattr(w, "gh", lambda *a, **k: "")
    assert w.claim_issue(12) is True
    z = _zeilen(f)
    assert len(z) == 1 and z[0]["event"] == "issue_claimed" and z[0]["issue"] == 12


def test_claim_issue_fehler_schreibt_nichts(tmp_path, monkeypatch):
    f = tmp_path / "audit.jsonl"
    monkeypatch.setattr(w, "AUDIT_LOG", str(f))

    def kaputt(*a, **k):
        raise RuntimeError("gh weg")

    monkeypatch.setattr(w, "gh", kaputt)
    assert w.claim_issue(12) is False
    assert not f.exists()
