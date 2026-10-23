import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_retarget", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_retarget"] = w
_spec.loader.exec_module(w)

ISSUE_BODY = ("### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `sandbox/a.py`\n\n"
              "### Fertig, wenn\n\n- [ ] Tests gruen\n")


class FakeGH:
    def __init__(self, issues):
        self.issues = list(issues)

    def __call__(self, *args, **kwargs):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else self.issues
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Test Issue"}
        if args[:2] == ("issue", "edit"):
            return None
        if args[:2] in (("pr", "list"), ("run", "list")) or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def test_retarget_task_ersetzt_nur_die_eigenen_stellen():
    task = ("Du bist Worker: Alt A. Schritt.\nmit „Worker: Alt A“ unter X\n"
            "----- Issue -----\nWorker: Alt A\n----- Ende Issue -----")
    neu = w.retarget_task(task, "Alt A", "Neu B")
    assert "Du bist Worker: Neu B." in neu
    assert "„Worker: Neu B“" in neu
    assert "\nWorker: Alt A\n" in neu
    assert "Du bist Worker: Alt A." not in neu
    assert w.retarget_task(task, "Alt A", "Alt A") == task
    assert w.retarget_task(task, "", "Neu B") == task


def test_main_sucht_nach_dem_ausweichen_nicht_neu(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "LITELLM_MODEL", "coder")
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
    monkeypatch.setattr(w, "WORKER", w.worker_name("coder"))
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    kandidat = {"number": 7, "title": "Test Issue", "body": ISSUE_BODY,
                "author": {"login": w.OWNER}, "labels": [{"name": "bereit"}]}
    monkeypatch.setattr(w, "gh", FakeGH([kandidat]))
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "open_pr", lambda n: True)
    monkeypatch.setattr(w, "choose_model", lambda *a: ("coder-free", ["coder: TimeoutError"]))
    echt = w.find_job
    suchen = []
    monkeypatch.setattr(w, "find_job", lambda state: suchen.append(1) or echt(state))
    gelaufen = []
    monkeypatch.setattr(w, "run_agent", lambda prompt: gelaufen.append(prompt) or 0)
    assert w.main() == 0
    assert len(suchen) == 1
    assert len(gelaufen) == 1
    assert f"Du bist Worker: {w.worker_name('coder-free')}." in gelaufen[0]
    assert f"Du bist Worker: {w.worker_name('coder')}." not in gelaufen[0]
    assert w.ACTIVE_MODEL == "coder-free"
