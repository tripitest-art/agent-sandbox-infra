import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

_spec = importlib.util.spec_from_file_location("worker_tick_frame_test", _SANDBOX / "worker_tick.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_frame_test"] = w
_spec.loader.exec_module(w)

FENCE = "`" * 3
ISSUE_BODY = (
    "### Ziel\n\nTest.\n\n"
    "### Betroffene Dateien\n\n- `sandbox/worker_tick.py`\n\n"
    "### Akzeptanztests\n\n"
    + FENCE + "python\ndef test_ok():\n    assert True\n" + FENCE + "\n"
)


def test_prepare_issue_frame_ohne_workdir_tut_nichts(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path / "fehlt"))
    monkeypatch.setattr(w, "gh", lambda *a: pytest.fail("kein gh ohne WORKDIR"))
    assert w.prepare_issue_frame(42) is None


def test_prepare_issue_frame_legt_branch_und_akzeptanztest_an(monkeypatch, tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    monkeypatch.setattr(w, "WORKDIR", str(workdir))
    monkeypatch.setattr(w, "gh", lambda *a: {"body": ISSUE_BODY})
    git_calls = []

    def fake_run(cmd, cwd=None, **kw):
        git_calls.append((list(cmd), cwd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    path = w.prepare_issue_frame(42)
    assert path == workdir / "tests" / "acceptance" / "test_issue_42.py"
    assert path.read_text(encoding="utf-8") == "def test_ok():\n    assert True\n"
    assert [c[0] for c in git_calls] == [
        ["git", "checkout", "-B", "feature/issue-42"],
        ["git", "add", "tests/acceptance/test_issue_42.py"],
        ["git", "commit", "-m", "Akzeptanztests aus #42"],
    ]


def test_autofix_pr_text_aktualisiert_unvollstaendigen_pr_ohne_llm(monkeypatch):
    edits = []

    def fake_gh(*args):
        if args[:2] == ("pr", "edit"):
            edits.append(args)
            return ""
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    monkeypatch.setattr(w, "gh", fake_gh)
    monkeypatch.setattr(w, "WORKER", "DeepSeek (LiteLLM coder)")
    monkeypatch.setattr(
        w,
        "pr_template",
        lambda: "## Was und warum\n\nCloses #\n\n## Wie getestet\n\n- [ ] pytest\n\n## Offene Punkte\n\n## Umgesetzt von / Review durch\n",
    )

    assert w.autofix_pr_text(112, 102, "## Was und warum\n\nCloses #\n") is True
    assert len(edits) == 1
    assert edits[0][:4] == ("pr", "edit", "112", "--body")
    new_body = edits[0][4]
    assert w.pr_text_problems(new_body, 102) == []


def test_autofix_pr_text_laesst_vollstaendigen_pr_unberuehrt(monkeypatch):
    monkeypatch.setattr(w, "gh", lambda *a: pytest.fail("gh darf bei vollständigem Text nicht laufen"))
    monkeypatch.setattr(w, "pr_text_problems", lambda body, no: [])
    assert w.autofix_pr_text(112, 102, "alles da") is False


def test_check_issue_result_repariert_fehlenden_pr_text_sofort(monkeypatch):
    edits = []

    def fake_gh(*args):
        if args[:2] == ("pr", "list"):
            return [{"number": 115, "body": "unvollständig"}]
        if args[:2] == ("pr", "edit"):
            edits.append(args)
            return ""
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    monkeypatch.setattr(w, "gh", fake_gh)
    monkeypatch.setattr(
        w,
        "pr_template",
        lambda: "## Was und warum\n\nCloses #\n\n## Wie getestet\n\n- [ ] pytest\n\n## Offene Punkte\n\n## Umgesetzt von / Review durch\n",
    )
    w.check_issue_result("issue102")
    assert len(edits) == 1 and edits[0][2] == "115"
