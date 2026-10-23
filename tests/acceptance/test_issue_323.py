import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_schlank", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_schlank"] = w
_spec.loader.exec_module(w)

ISSUE_BODY = ("### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `sandbox/a.py`\n\n"
              "### Fertig, wenn\n\n- [ ] Tests gruen\n")


class FakeGH:
    """Kandidaten- und `in-arbeit`-Liste getrennt; Kandidat mit Autor und Dateiliste (Regel 8)."""

    def __init__(self, issues=(), prs=()):
        self.issues, self.prs = list(issues), list(prs)

    def __call__(self, *args, **kwargs):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else self.issues
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Test Issue"}
        if args[:2] == ("issue", "edit"):
            return None
        if args[:2] == ("pr", "list"):
            return self.prs
        if args[:2] == ("run", "list") or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _kandidat(nummer=42):
    return {"number": nummer, "title": "Test Issue", "body": ISSUE_BODY,
            "author": {"login": w.OWNER}, "labels": [{"name": "bereit"}]}


def _pr(review_body):
    return {"number": 103, "headRefName": "feature/issue-100", "headRefOid": "3373e35",
            "isCrossRepository": False, "commits": [{"committedDate": "2026-09-29T21:37:00Z"}],
            "reviews": [{"submittedAt": "2026-09-29T21:39:23Z", "body": review_body,
                         "author": {"login": w.OWNER}, "commit": {"oid": "3373e35"}}],
            "title": "T", "body": "x"}


def test_agent_test_cmd_pytest_und_fremder_befehl(monkeypatch):
    monkeypatch.setattr(w, "TEST_CMD", "python -m pytest -q")
    assert w.agent_test_cmd() == "python -m pytest -q --tb=short"
    assert (w.agent_test_cmd("tests/acceptance/test_issue_7.py")
            == "python -m pytest -q tests/acceptance/test_issue_7.py -x --tb=short")
    monkeypatch.setattr(w, "TEST_CMD", "python -m pytest -q --tb=line")
    assert w.agent_test_cmd() == "python -m pytest -q --tb=line"
    assert (w.agent_test_cmd("tests/acceptance/test_issue_7.py")
            == "python -m pytest -q --tb=line tests/acceptance/test_issue_7.py -x")
    monkeypatch.setattr(w, "TEST_CMD", "make test")
    assert w.agent_test_cmd() == "make test"
    assert w.agent_test_cmd("tests/acceptance/test_issue_7.py") == "make test"


def test_issue_auftrag_erst_fokussiert_dann_voll_ohne_merge_satz(monkeypatch):
    monkeypatch.setattr(w, "TEST_CMD", "python -m pytest -q")
    monkeypatch.setattr(w, "gh", FakeGH(issues=[_kandidat()]))
    key, task, _, _ = w.issue_job()
    assert key == "issue42"
    fokus = "`python -m pytest -q tests/acceptance/test_issue_42.py -x --tb=short`"
    voll = "`python -m pytest -q --tb=short`"
    assert fokus in task and voll in task and task.index(fokus) < task.index(voll)
    assert "Never approve or merge the PR yourself" not in task
    assert w.PUSH_RULE in task


def test_review_wants_pr_text():
    assert w.review_wants_pr_text("**Änderungen nötig**\n\n### Muss\n- `Closes #100` fehlt im PR-Text")
    assert w.review_wants_pr_text("Bitte die PR-Beschreibung ergänzen.")
    assert not w.review_wants_pr_text("**Änderungen nötig**\n\n### Muss\n- Test fehlt in tests/x.py\n\n"
                                      "Worker: DeepSeek, Reviewer: GLM (andere Modellfamilie)")
    assert not w.review_wants_pr_text(None)
    assert not w.review_wants_pr_text("")


def test_nacharbeit_vorlage_nur_wenn_das_review_den_text_anspricht(monkeypatch):
    monkeypatch.setattr(w, "pr_template", lambda: "## Was und warum\nMARKER-VORLAGE-91c2\n")
    nur_code = "**Änderungen nötig**\n\n### Muss\n- Funktion `f` prüft None nicht (sandbox/a.py)."
    monkeypatch.setattr(w, "gh", FakeGH(prs=[_pr(nur_code)]))
    _, task, n, _ = w.rework_job()
    assert n == 103
    assert "MARKER-VORLAGE-91c2" not in task
    assert "gh pr edit 103" in task
    assert w.agent_test_cmd() in task

    mit_text = "**Änderungen nötig**\n\n### Muss\n- `Closes #100` fehlt im PR-Text."
    monkeypatch.setattr(w, "gh", FakeGH(prs=[_pr(mit_text)]))
    _, task, _, _ = w.rework_job()
    assert "MARKER-VORLAGE-91c2" in task


def test_main_haengt_run_rules_ohne_test_duty_an(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "gh", FakeGH(issues=[_kandidat(7)]))
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "open_pr", lambda n: True)
    monkeypatch.setattr(w, "choose_model", lambda *a: ("coder", []))
    gelaufen = []
    monkeypatch.setattr(w, "run_agent", lambda prompt: gelaufen.append(prompt) or 0)
    assert w.main() == 0
    assert len(gelaufen) == 1
    assert gelaufen[0].endswith(w.RUN_RULES)
    assert "TEST DUTY" not in gelaufen[0]
    assert "3. ESCALATION: Work without asking back." in w.RUN_RULES
