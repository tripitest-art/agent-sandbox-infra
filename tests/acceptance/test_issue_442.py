import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "gc.py"
_spec = importlib.util.spec_from_file_location("gc_squash", _PATH)
gc = importlib.util.module_from_spec(_spec)
sys.modules["gc_squash"] = gc
_spec.loader.exec_module(gc)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, name, text, msg=None):
    (repo / name).write_text(text, encoding="utf-8")
    git("add", name, cwd=repo)
    git("commit", "-m", msg or name, cwd=repo)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Server-Klon mit vier Branches; „GitHub“ (seed) squash-mergt einen davon."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for rolle in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{rolle}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{rolle}_EMAIL", "test@example.test")
    origin, seed, klon = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "klon"
    git("init", "--bare", "-b", "main", str(origin))
    git("init", "-b", "main", str(seed))
    commit(seed, "README.txt", "x\n")
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    git("clone", str(origin), str(klon))
    for name, datei in (("feature/fertig", "a.txt"), ("feature/offen", "b.txt"), ("feature/im-worktree", "c.txt")):
        git("checkout", "-q", "-b", name, "origin/main", cwd=klon)
        commit(klon, datei, name + "\n")
    git("checkout", "-q", "-b", "feature/leer", "origin/main", cwd=klon)
    git("checkout", "-q", "main", cwd=klon)
    git("worktree", "add", "-q", str(tmp_path / "wt"), "feature/im-worktree", cwd=klon)
    commit(seed, "a.txt", "feature/fertig\n", "Fertig (#1)")         # Squash-Merge von feature/fertig
    commit(seed, "c.txt", "feature/im-worktree\n", "Worktree (#2)")  # auch gemergt, aber ausgecheckt
    git("push", "origin", "main", cwd=seed)
    return klon


def _branches(klon):
    return sorted(git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=klon).split())


def test_squash_gemergter_branch_wird_geloescht(repo):
    assert gc.cleanup_squashed_branches(repo) == ["feature/fertig"]
    assert _branches(repo) == ["feature/im-worktree", "feature/leer", "feature/offen", "main"]


def test_fetch_fehler_loescht_nichts(repo):
    git("remote", "set-url", "origin", str(repo.parent / "weg.git"), cwd=repo)
    assert gc.cleanup_squashed_branches(repo) == []
    assert "feature/fertig" in _branches(repo)


def test_run_gc_zaehlt_squash_branches(repo, tmp_path, capsys):
    claims = tmp_path / "claims"
    claims.mkdir()
    stats = gc.run_gc(claims, [repo])
    # feature/leer löscht schon cleanup_merged_branches (ohne eigene Commits), feature/fertig der neue Squash-Weg
    assert stats == {"claims": 0, "branches": 2, "repos": 1}
    out = capsys.readouterr().out
    assert "feature/fertig" in out and "feature/leer" in out
    assert _branches(repo) == ["feature/im-worktree", "feature/offen", "main"]


def _klon(ort, name, alter_s, now):
    d = ort / name
    (d / ".git").mkdir(parents=True)
    os.utime(d, (now - alter_s, now - alter_s))
    return d


def test_streukopien_findet_nur_alte_fremde_klone(tmp_path):
    now = 2_000_000_000.0
    tmp, home = tmp_path / "tmp", tmp_path / "home"
    tmp.mkdir()
    home.mkdir()
    alt = _klon(tmp, "sc-digger", 3 * 86400, now)
    _klon(tmp, "frisch", 600, now)
    (tmp / "kein-klon").mkdir()
    wt = home / "repo"
    (wt).mkdir()
    (wt / ".git").write_text("gitdir: /x\n", encoding="utf-8")
    os.utime(wt, (now - 2 * 86400, now - 2 * 86400))
    bekannt = _klon(home, "arbeit", 5 * 86400, now)
    got = gc.streukopien(orte=[str(tmp), str(home), str(tmp_path / "fehlt")], bekannt=[str(bekannt)], now=now)
    assert got == sorted([str(alt), str(wt)])


def test_neue_streukopien_meldet_jede_nur_einmal(tmp_path):
    marke = str(tmp_path / "marke.json")
    assert gc.neue_streukopien(["/tmp/a", "/tmp/b"], marke) == ["/tmp/a", "/tmp/b"]
    assert gc.neue_streukopien(["/tmp/b", "/tmp/a"], marke) == []
    assert json.loads(Path(marke).read_text(encoding="utf-8")) == ["/tmp/a", "/tmp/b"]
    assert gc.neue_streukopien(["/tmp/b"], marke) == []
    assert gc.neue_streukopien(["/tmp/a", "/tmp/b"], marke) == ["/tmp/a"], "wiedergekehrt gilt als neu"
    Path(marke).write_text("kaputt", encoding="utf-8")
    assert gc.neue_streukopien(["/tmp/a"], marke) == ["/tmp/a"]


def test_main_meldet_neue_streukopien(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(gc, "run_gc", lambda claim_dir, dirs, run=None: {"claims": 0, "branches": 0, "repos": 0})
    monkeypatch.setattr(gc, "streukopien", lambda bekannt=(), **k: ["/tmp/sc-digger", "/home/worker/repo"])
    monkeypatch.setattr(gc, "STREU_MARKE", str(tmp_path / "marke.json"))
    monkeypatch.setenv("AGENT_PROJECTS", str(tmp_path / "fehlt.json"))
    assert gc.main() == 0
    zeilen = [z for z in capsys.readouterr().out.splitlines() if z.startswith("⚠ Stephan:")]
    assert zeilen == ["⚠ Stephan: Streukopien außerhalb der Arbeitsordner (nicht gelöscht): "
                      "/tmp/sc-digger, /home/worker/repo"]
    assert gc.main() == 0
    assert not [z for z in capsys.readouterr().out.splitlines() if z.startswith("⚠ Stephan:")]
