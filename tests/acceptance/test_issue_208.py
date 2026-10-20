"""Akzeptanztest: Ein vorhandener Issue-Branch bekommt vor dem Lauf den aktuellen origin/main."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_branch_sync", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_branch_sync"] = w
_spec.loader.exec_module(w)

BRANCH = "feature/issue-9"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                          text=True).stdout.strip()


def schreiben(cwd, name, inhalt, nachricht):
    (Path(cwd) / name).write_text(inhalt, encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", nachricht)


@pytest.fixture
def klon(tmp_path, monkeypatch):
    """origin (bare), ein zweiter Klon `andere` für neue Commits auf main und der Arbeitsklon `arbeit`."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    andere = tmp_path / "andere"
    git(tmp_path, "clone", "-q", str(origin), str(andere))
    for repo in (andere,):
        git(repo, "config", "user.email", "t@example.org")
        git(repo, "config", "user.name", "Test")
    schreiben(andere, "a.txt", "a\n", "Start")
    git(andere, "push", "-q", "origin", "HEAD:main")
    arbeit = tmp_path / "arbeit"
    git(tmp_path, "clone", "-q", str(origin), str(arbeit))
    git(arbeit, "config", "user.email", "t@example.org")
    git(arbeit, "config", "user.name", "Test")
    # Lokaler Branch mit eigener Arbeit, nie gepusht (wie auf dem Server nach einem Lauf).
    git(arbeit, "checkout", "-q", "-b", BRANCH)
    schreiben(arbeit, "b.txt", "arbeit\n", "Umsetzung")
    monkeypatch.setattr(w, "WORKDIR", str(arbeit))
    monkeypatch.setattr(w, "ENV", None)
    return andere, arbeit


def main_voranbringen(andere, name, inhalt):
    schreiben(andere, name, inhalt, f"main: {name}")
    git(andere, "push", "-q", "origin", "HEAD:main")


def test_neuer_main_kommt_in_den_vorhandenen_branch(klon):
    andere, arbeit = klon
    main_voranbringen(andere, "fix.txt", "behoben\n")

    assert w.sync_issue_branch_with_main(9) is True

    assert (arbeit / "fix.txt").read_text(encoding="utf-8") == "behoben\n"
    assert (arbeit / "b.txt").exists()                      # eigene Arbeit bleibt
    assert git(arbeit, "branch", "--show-current") == BRANCH
    git(arbeit, "merge-base", "--is-ancestor", "origin/main", "HEAD")   # sonst CalledProcessError
    assert git(arbeit, "status", "--porcelain") == ""


def test_schon_aktueller_branch_bekommt_keinen_commit(klon):
    andere, arbeit = klon
    vorher = git(arbeit, "rev-parse", "HEAD")

    assert w.sync_issue_branch_with_main(9) is True
    assert git(arbeit, "rev-parse", "HEAD") == vorher


def test_konflikt_bricht_den_merge_ab_und_meldet_false(klon):
    andere, arbeit = klon
    schreiben(arbeit, "a.txt", "aus dem Branch\n", "Branch ändert a.txt")
    main_voranbringen(andere, "a.txt", "aus main\n")
    vorher = git(arbeit, "rev-parse", "HEAD")

    assert w.sync_issue_branch_with_main(9) is False

    assert git(arbeit, "rev-parse", "HEAD") == vorher       # Branch unverändert
    assert git(arbeit, "status", "--porcelain") == ""        # kein halber Merge
    assert not (arbeit / ".git" / "MERGE_HEAD").exists()
