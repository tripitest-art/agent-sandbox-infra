import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_rework_drift", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_rework_drift"] = w
_spec.loader.exec_module(w)


def test_verify_rework_pr_open_and_matching(monkeypatch):
    monkeypatch.setattr(w, "gh", lambda *a: {"state": "OPEN", "headRefOid": "abc1234"})
    ok, reason = w.verify_rework_pr(42, "abc1234")
    assert ok is True
    assert reason == ""


def test_verify_rework_pr_closed(monkeypatch):
    monkeypatch.setattr(w, "gh", lambda *a: {"state": "CLOSED", "headRefOid": "abc1234"})
    ok, reason = w.verify_rework_pr(42, "abc1234")
    assert ok is False
    assert "nicht mehr OPEN" in reason or "CLOSED" in reason


def test_verify_rework_pr_commit_drift(monkeypatch):
    monkeypatch.setattr(w, "gh", lambda *a: {"state": "OPEN", "headRefOid": "different999"})
    ok, reason = w.verify_rework_pr(42, "abc1234")
    assert ok is False
    assert "HEAD hat sich geändert" in reason or "different999" in reason
