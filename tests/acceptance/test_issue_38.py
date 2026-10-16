import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)


def _issue(n):
    return {"number": n, "title": f"Issue {n}", "body": "### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `sandbox/beispiel.py`\n",
            "author": {"login": "tripitest-art"}}


class Board:
    """GitHub in klein: das Übernehmen ändert wirklich die Labels, damit ein zweiter
    Suchlauf im selben Takt das Issue nicht mehr als `bereit` sieht."""

    def __init__(self, numbers):
        self.labels = {n: {"worker-task", "bereit", "agent-qwen"} for n in numbers}
        self.claims = []

    def __call__(self, *args):
        if args[:2] == ("issue", "list"):
            if "in-arbeit" in args:
                return [_issue(n) for n, l in sorted(self.labels.items()) if "in-arbeit" in l]
            return [_issue(n) for n, l in sorted(self.labels.items()) if "bereit" in l]
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": x} for x in self.labels[int(args[2])]]}
        if args[:2] == ("issue", "edit"):
            n = int(args[2])
            self.claims.append(n)
            self.labels[n].discard("bereit")
            self.labels[n].add("in-arbeit")
            return None
        if args[:2] == ("pr", "list"):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _main(monkeypatch, board, prompts, model, gh=None):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "WORKER", w.WORKER)     # use_model() ändert den Namen global
    monkeypatch.setattr(w, "gh", gh or board)
    monkeypatch.setattr(w, "load_state", lambda: {})
    monkeypatch.setattr(w, "save_state", lambda state: None)
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "choose_model", lambda: model)
    monkeypatch.setattr(w, "run_agent", lambda prompt: prompts.append(prompt) or 0)
    monkeypatch.setattr(w, "WORKDIR", "/kein/klon")       # PR-Vorlage nicht lesbar
    monkeypatch.setattr(w, "BASE_WORKDIR", "/kein/klon")
    return w.main()


def test_issue_job_claims_nothing(monkeypatch):
    # Die Auswahl darf keine Labels ändern: sonst ist das Issue weg, bevor ein Gate entschieden hat.
    board = Board([101])
    monkeypatch.setattr(w, "gh", board)
    key, _task, _pr, _before = w.issue_job()
    assert key == "issue101"
    assert board.claims == []
    assert "bereit" in board.labels[101]


def test_no_model_claims_nothing(monkeypatch):
    # Kein Modell antwortet: kein Claim, kein Lauf, Issue bleibt bereit.
    board, prompts = Board([101]), []
    assert _main(monkeypatch, board, prompts, (None, ["coder: HTTP 500"])) == 0
    assert board.claims == [] and prompts == []


def test_model_fallback_claims_the_same_issue_once(monkeypatch):
    # Ausweichmodell: der zweite Suchlauf muss dasselbe Issue finden, nicht ein zweites übernehmen.
    board, prompts = Board([101, 102]), []
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
    assert _main(monkeypatch, board, prompts, ("coder-or", [])) == 0
    assert board.claims == [101]
    assert len(prompts) == 1 and "Du setzt Issue #101 um" in prompts[0]


def test_failed_claim_runs_no_agent(monkeypatch):
    # Scheitert das Übernehmen (403), läuft kein Agent mit noch freiem Issue.
    board, prompts = Board([101]), []
    real = board.__call__

    def gh(*args):
        if args[:2] == ("issue", "edit"):
            raise w.subprocess.CalledProcessError(1, list(args), stderr="HTTP 403")
        return real(*args)

    assert _main(monkeypatch, board, prompts, ("coder", []), gh=gh) == 0
    assert prompts == [] and "bereit" in board.labels[101]
