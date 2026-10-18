import importlib.util
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SANDBOX = _ROOT / "sandbox"


def _load(name, pfad):
    spec = importlib.util.spec_from_file_location(name, pfad)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load("worker_tick_rahmen1", _SANDBOX / "worker_tick.py")
guard = _load("acceptance_guard_rahmen1", _ROOT / ".github" / "scripts" / "acceptance_guard.py")

BLOCK = "def test_a():\n    assert 1 + 1 == 2"
ISSUE = f"### Ziel\n\nEtwas.\n\n### Akzeptanztests\n\n```python\n{BLOCK}\n```\n\n### Fertig, wenn\n\n- x\n"

BODIES = [
    None, "", "kein Block",
    ISSUE,
    ISSUE.replace("\n", "\r\n"),
    "### Akzeptanztests\n\n```python\ndef test_a():\n    assert 1   \n```\n\n### Fertig, wenn\n",
    "## akzeptanztests (pytest)\n\n~~~python\nx = 1\n~~~\n",
    "#### Akzeptanztests\n```\nerst\n```\n```\nzweit\n```\n",
    "### Akzeptanztests\n\n_No response_\n\n### Kontext\n```python\nnicht\n```\n",
    "### Akzeptanztests\n```python\n# ### kein Titel\nx = 1\n```\n### Ende\n",
    "### Akzeptanztests\n```python\n\n\n```\n",
]


def test_acceptance_block_stimmt_mit_acceptance_guard_ueberein():
    for body in BODIES:
        assert w.acceptance_block(body) == guard.extract_acceptance_block(body), repr(body)
    assert w.acceptance_block(ISSUE) == BLOCK


class Git:
    def __init__(self, remote=False, lokal=False):
        self.remote, self.lokal, self.calls = remote, lokal, []

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        rc = 0
        if list(cmd[:3]) == ["git", "rev-parse", "--verify"]:
            ref = cmd[-1]
            gefunden = (ref.startswith("refs/remotes/") and self.remote) or \
                       (ref.startswith("refs/heads/") and self.lokal)
            rc = 0 if gefunden else 1
        return subprocess.CompletedProcess(cmd, rc, "", "")

    @property
    def cmds(self):
        return [c for c, _ in self.calls]


def _setup(monkeypatch, tmp_path, body=ISSUE, **git):
    arbeit = tmp_path / "wt"
    arbeit.mkdir()
    monkeypatch.setattr(w, "WORKDIR", str(arbeit))
    gefragt = []

    def gh(*args):
        gefragt.append(args)
        assert args[:2] == ("issue", "view") and args[2] == "7"
        return {"body": body}

    monkeypatch.setattr(w, "gh", gh)
    fake = Git(**git)
    monkeypatch.setattr(w.subprocess, "run", fake)
    return arbeit, fake, gefragt


DATEI = "tests/acceptance/test_issue_7.py"
FETCH = ["git", "fetch", "-q", "origin"]
REMOTE = ["git", "rev-parse", "--verify", "-q", "refs/remotes/origin/feature/issue-7"]
LOKAL = ["git", "rev-parse", "--verify", "-q", "refs/heads/feature/issue-7"]
COMMIT = [["git", "add", DATEI], ["git", "commit", "-q", "-m", "Akzeptanztests aus #7"]]


def test_neuer_branch_ab_origin_main_und_commit_des_tests(monkeypatch, tmp_path):
    arbeit, git, gefragt = _setup(monkeypatch, tmp_path)
    assert w.prepare_issue_branch(7) is True
    assert git.cmds == [FETCH, REMOTE, LOKAL,
                        ["git", "checkout", "-q", "-B", "feature/issue-7", "origin/main"], *COMMIT]
    assert all(kw.get("cwd") == str(arbeit) for _, kw in git.calls)
    assert (arbeit / DATEI).read_text(encoding="utf-8") == BLOCK + "\n"
    assert gefragt[0] == ("issue", "view", "7", "--json", "body")


def test_vorhandener_zweig_auf_origin_wird_fortgesetzt(monkeypatch, tmp_path):
    arbeit, git, _ = _setup(monkeypatch, tmp_path, remote=True)
    assert w.prepare_issue_branch(7) is True
    assert git.cmds == [FETCH, REMOTE,
                        ["git", "checkout", "-q", "-B", "feature/issue-7", "origin/feature/issue-7"], *COMMIT]


def test_vorhandener_lokaler_zweig_wird_fortgesetzt(monkeypatch, tmp_path):
    arbeit, git, _ = _setup(monkeypatch, tmp_path, lokal=True)
    assert w.prepare_issue_branch(7) is True
    assert git.cmds == [FETCH, REMOTE, LOKAL, ["git", "checkout", "-q", "feature/issue-7"], *COMMIT]


def test_gleicher_inhalt_gibt_keinen_zweiten_commit(monkeypatch, tmp_path):
    arbeit, git, _ = _setup(monkeypatch, tmp_path)
    (arbeit / "tests" / "acceptance").mkdir(parents=True)
    (arbeit / DATEI).write_bytes((BLOCK + "\n").encode("utf-8"))
    assert w.prepare_issue_branch(7) is True
    assert git.cmds == [FETCH, REMOTE, LOKAL,
                        ["git", "checkout", "-q", "-B", "feature/issue-7", "origin/main"]]


def test_abweichende_datei_wird_zeichengenau_wiederhergestellt(monkeypatch, tmp_path):
    arbeit, git, _ = _setup(monkeypatch, tmp_path)
    (arbeit / "tests" / "acceptance").mkdir(parents=True)
    (arbeit / DATEI).write_text("def test_a():\n    assert True\n", encoding="utf-8")
    assert w.prepare_issue_branch(7) is True
    assert (arbeit / DATEI).read_text(encoding="utf-8") == BLOCK + "\n"
    assert git.cmds[-2:] == COMMIT


def test_ohne_block_im_issue_laeuft_kein_git_befehl(monkeypatch, tmp_path):
    arbeit, git, _ = _setup(monkeypatch, tmp_path, body="### Ziel\n\nNur Text.\n")
    assert w.prepare_issue_branch(7) is False
    assert git.calls == []
    assert not (arbeit / "tests").exists()


class FakeGH:
    def __init__(self, issues):
        self.issues = issues

    def __call__(self, *args):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else self.issues
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def test_auftragstext_nennt_den_fertigen_branch_statt_der_schritte(monkeypatch):
    monkeypatch.setattr(w, "HOTSPOT", "")
    monkeypatch.setattr(w, "TEST_CMD", "python -m pytest -q")
    monkeypatch.setattr(w, "template_rules", lambda: "")
    issue = {"number": 102, "title": "Titel", "author": {"login": "tripitest-art"}, "labels": [],
             "body": "### Betroffene Dateien\n\n- sandbox/foo.py\n\n" + ISSUE}
    monkeypatch.setattr(w, "gh", FakeGH([issue]))
    key, task, pr_no, sha = w.issue_job()
    assert key == "issue102"
    assert ("Der Takt hat den Branch feature/issue-102 ausgecheckt und den Akzeptanztest "
            "bereits als Commit gesichert.") in task
    assert "git checkout -b" not in task
    assert 'git commit -m "Akzeptanztests aus #102"' not in task
    assert "gh issue edit 102 --add-label in-arbeit --remove-label bereit" in task
    assert issue["body"] in task
