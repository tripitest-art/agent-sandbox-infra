import importlib.util
from pathlib import Path
import sys

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

_spec = importlib.util.spec_from_file_location("worker_tick_prio_test", _SANDBOX / "worker_tick.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_prio_test"] = w
_spec.loader.exec_module(w)


def test_prio_konstanten_vorhanden():
    assert getattr(w, "DEFAULT_PRIO_SCORE", None) == 100.0
    assert getattr(w, "UNBLOCK_WEIGHT", None) == 150.0
    assert getattr(w, "AGING_WEIGHT_PER_DAY", None) == 5.0
    assert getattr(w, "BLOCKED_ISSUES_LIMIT", None) == 30
    weights = getattr(w, "PRIO_WEIGHTS", {})
    assert weights.get("prio:kritisch") == 1000.0
    assert weights.get("prio:hoch") == 300.0
    assert weights.get("prio:normal") == 100.0
    assert weights.get("prio:niedrig") == 0.0


def test_parse_dependencies_ignoriert_codebloecke():
    body = (
        "Hier ist Text.\n"
        + "Wartet "
        + "auf: #12, #15\n"
        + "```python\n"
        + "Wartet "
        + "auf: #99\n"
        + "```\n"
    )
    assert w.parse_dependencies(body) == [12, 15]
    assert w.parse_dependencies(None) == []


def test_count_unblock_dependencies():
    blocked = [
        {"number": 101, "body": "Wartet " + "auf: #42, #43\n"},
        {"number": 102, "body": "Wartet " + "auf: #42\n"},
        {"number": 103, "body": "Keine Abhaengigkeit\n"},
    ]
    counts = w.count_unblock_dependencies(blocked)
    assert counts.get(42) == 2
    assert counts.get(43) == 1
    assert counts.get(99) == 0 or 99 not in counts


def test_calculate_issue_score_mit_labels_und_unblock():
    fixed_now = 1700000000.0
    created_iso = "2023-11-12T22:13:20Z"
    issue_normal = {
        "number": 10,
        "labels": [{"name": "worker-task"}, {"name": "bereit"}],
        "createdAt": created_iso,
    }
    assert abs(w.calculate_issue_score(issue_normal, {}, now=fixed_now) - 110.0) < 0.01

    issue_hoch = {
        "number": 20,
        "labels": [{"name": "prio:hoch"}],
        "createdAt": created_iso,
    }
    score_hoch = w.calculate_issue_score(issue_hoch, {20: 1}, now=fixed_now)
    assert abs(score_hoch - 460.0) < 0.01


def test_issue_job_bevorzugt_hoeheren_score(monkeypatch):
    fixed_now = 1700000000.0
    issue_low = {
        "number": 5,
        "title": "Low Prio",
        "labels": [{"name": "prio:niedrig"}],
        "author": {"login": "tripitest-art"},
        "body": "### Betroffene Dateien\n- `a.py`\n",
        "createdAt": "2023-11-14T22:13:20Z",
    }
    issue_high = {
        "number": 6,
        "title": "High Prio",
        "labels": [{"name": "prio:hoch"}],
        "author": {"login": "tripitest-art"},
        "body": "### Betroffene Dateien\n- `b.py`\n",
        "createdAt": "2023-11-14T22:13:20Z",
    }

    def fake_gh(*args, **kw):
        if args[:2] == ("issue", "list") and "in-arbeit" in args:
            return []
        if args[:2] == ("issue", "list") and "blockiert" in args:
            return []
        if args[:2] == ("issue", "list"):
            return [issue_low, issue_high]
        return None

    monkeypatch.setattr(w, "gh", fake_gh)
    monkeypatch.setattr(w, "jetzt_s", lambda: fixed_now)
    key, task, pr_no, pr_snap = w.issue_job(now=fixed_now)
    assert key == "issue6"
