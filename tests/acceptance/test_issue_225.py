import subprocess
import sys
from pathlib import Path
import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

from sandbox import gc


def test_prune_worktrees_ruft_git_worktree_prune_auf():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ok = gc.prune_worktrees("/tmp/repo", run=fake_run)
    assert ok is True
    assert ["git", "-C", "/tmp/repo", "worktree", "prune"] in calls or ["git", "worktree", "prune"] in calls


def test_cleanup_merged_branches_ignoriert_main_und_loescht_feature_branch():
    deleted = []

    def fake_run(cmd, **kwargs):
        cmd_list = list(cmd)
        if "--merged" in cmd_list:
            stdout = "* main\n  feature/issue-100\n  master\n"
            return subprocess.CompletedProcess(cmd, 0, stdout, "")
        if "-d" in cmd_list:
            deleted.append(cmd_list[-1])
            return subprocess.CompletedProcess(cmd, 0, "Deleted branch", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    geloescht = gc.cleanup_merged_branches("/tmp/repo", main_branch="main", run=fake_run)
    assert geloescht == ["feature/issue-100"]
    assert "main" not in deleted
    assert "master" not in deleted


def test_prune_claims_loescht_ueberfaellige_done_dateien(tmp_path):
    claim_dir = tmp_path / "claims"
    claim_dir.mkdir(parents=True, exist_ok=True)
    alt = claim_dir / "issue1.done"
    neu = claim_dir / "issue2.done"
    alt.write_text("100", encoding="utf-8")
    neu.write_text("200", encoding="utf-8")

    # Referenzzeitpunkt 1000, alt hat mtime 100, neu hat mtime 900, max_age = 500
    now_ref = 1000.0
    import os
    os.utime(alt, (100.0, 100.0))
    os.utime(neu, (900.0, 900.0))

    geloescht = gc.prune_claims(claim_dir, max_age_seconds=500.0, now=now_ref)
    assert geloescht == 1
    assert not alt.exists()
    assert neu.exists()


def test_run_gc_aggregiert_ergebnisse(tmp_path):
    claim_dir = tmp_path / "claims"
    claim_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    def fake_run(cmd, **kwargs):
        cmd_list = list(cmd)
        if "--merged" in cmd_list:
            return subprocess.CompletedProcess(cmd, 0, "  feature/old\n* main\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    stats = gc.run_gc(claim_dir, [repo_dir], run=fake_run)
    assert isinstance(stats, dict)
    assert stats.get("branches") == 1
    assert stats.get("repos") == 1
