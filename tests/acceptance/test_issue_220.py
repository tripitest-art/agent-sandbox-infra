import subprocess
import sys
from pathlib import Path
import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

from sandbox.core import claims, git_ops


def test_claim_job_und_release_im_selben_prozess(tmp_path):
    claim_dir = tmp_path / "claims"
    assert claims.claim_job("issue123", claim_dir, pid=1000) is True
    # Zweiter Claim mit anderer PID scheitert, wenn Prozess als lebend gilt
    # (hier mit monkeypatch oder toter PID getestet)
    assert claims.job_done_anywhere("issue123", claim_dir) is False
    claims.mark_job_done("issue123", claim_dir, pid=1000)
    assert claims.job_done_anywhere("issue123", claim_dir) is True
    assert claims.release_job("issue123", claim_dir, pid=1000) is True


def test_release_job_loescht_fremde_belegung_nicht(tmp_path):
    claim_dir = tmp_path / "claims"
    claim_dir.mkdir(parents=True, exist_ok=True)
    claim_file = claim_dir / "issue99.claim"
    claim_file.write_text("99999", encoding="utf-8")
    assert claims.release_job("issue99", claim_dir, pid=1234) is False
    assert claim_file.exists()


def test_cleanup_claims_loescht_alte_done_dateien(tmp_path):
    claim_dir = tmp_path / "claims"
    claim_dir.mkdir(parents=True, exist_ok=True)
    alt_done = claim_dir / "alt.done"
    alt_done.write_text("1", encoding="utf-8")
    # max_age_days = 0 löscht alle vorhandenen .done-Dateien
    geloescht = claims.cleanup_claims(claim_dir, max_age_days=0)
    assert geloescht >= 1
    assert not alt_done.exists()


def test_clean_worktree_ruft_reset_und_clean_auf():
    calls = []

    def fake_run(cmd):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert git_ops.clean_worktree("/tmp/wd", fake_run) is True
    assert calls == [
        ["git", "reset", "--hard", "HEAD"],
        ["git", "clean", "-fd"],
    ]


def test_get_current_head_liefert_getrimmten_sha():
    def fake_run(cmd):
        return subprocess.CompletedProcess(cmd, 0, "  abc1234567\n", "")

    assert git_ops.get_current_head("/tmp/wd", fake_run) == "abc1234567"
