import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

REVIEW = "**Änderungen nötig**\n\n**Muss**\n\n1. Fehlt noch."
REWORK_KEY = "pr103@2026-09-29T21:39:23Z"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _good_body(issue_no=100):
    """Vollständiger PR-Text: sonst wäre der PR ein Text-Fix-Auftrag und nicht die Nacharbeit."""
    text = (REPO_ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    return text.replace("Closes #", f"Closes #{issue_no}") + f"\nWorker: {w.WORKER}\n"


def _issue(n=102):
    return {"number": n, "title": f"Issue {n}", "body": "### Ziel\n\nEtwas.",
            "author": {"login": "tripitest-art"}}


def _pr():
    return {"number": 103, "headRefName": "feature/issue-100-x", "headRefOid": "3373e35",
            "isCrossRepository": False, "title": "T", "body": _good_body(),
            "commits": [{"committedDate": "2026-09-29T21:37:00Z"}],
            "reviews": [{"submittedAt": "2026-09-29T21:39:23Z", "body": REVIEW,
                         "author": {"login": "tripitest-art"}, "commit": {"oid": "3373e35"}}]}


class Board:
    def __init__(self, issues):
        self.issues = issues          # {Nummer: Labelmenge}; andere Issues gibt es auch
        self.claims = []

    def labels_of(self, n):
        return self.issues.get(n, {"worker-task", "bereit", "agent-qwen"})

    def __call__(self, *args):
        if args[:2] == ("pr", "list"):
            return [_pr()]
        if args[:2] == ("issue", "list"):
            if "in-arbeit" in args:
                return []
            return [_issue(n) for n, l in sorted(self.issues.items()) if "bereit" in l]
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": x} for x in self.labels_of(int(args[2]))]}
        if args[:2] == ("issue", "edit"):
            self.claims.append(int(args[2]))
            return None
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def test_find_job_skips_parked_rework(monkeypatch, tmp_path):
    # Geparkte Nacharbeit darf die Auswahl nicht blockieren: das Issue kommt dran.
    # `job_parked` meldet über log_on_change, und das speichert den Zustand — STATE muss also
    # schreibbar sein (wie im Zwillings-Test), nicht /root/worker-state.json.
    board = Board({102: {"worker-task", "bereit", "agent-qwen"}})
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "gh", board)
    job = w.find_job({REWORK_KEY: w.MAX_TRIES})
    assert job and job[0] == "issue102"


def test_parked_rework_does_not_stop_the_tick(monkeypatch, capsys):
    # Der ganze Takt lief bisher leer, sobald der erste Kandidat geparkt war.
    board, prompts = Board({102: {"worker-task", "bereit", "agent-qwen"}}), []
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "gh", board)
    monkeypatch.setattr(w, "load_state", lambda: {REWORK_KEY: w.MAX_TRIES})
    monkeypatch.setattr(w, "save_state", lambda state: None)
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "choose_model", lambda: ("coder", []))
    monkeypatch.setattr(w, "run_agent", lambda prompt: prompts.append(prompt) or 0)

    assert w.main() == 0
    assert len(prompts) == 1 and "Du setzt Issue #102 um" in prompts[0]
    # Geparkt wird gemeldet, nicht still (Regel 7).
    out = capsys.readouterr().out
    assert REWORK_KEY in out and "wartet auf Stephan" in out
