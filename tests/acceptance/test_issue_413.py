import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORIEREN = shutil.ignore_patterns(".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache")


def _env():
    # Keine GIT_*-Variablen von außen (ein Hook setzt etwa GIT_DIR); eine feste Identität, damit
    # ein ungewollter Commit wirklich entsteht und nicht nur an fehlender Identität scheitert.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"})
    return env


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=_env(), check=True,
                          capture_output=True, text=True).stdout


def _wegwerf_repo(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT, repo, ignore=IGNORIEREN)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Basis")
    (repo / "uncommittet.txt").write_text("Arbeit, die nicht in einen Commit gehört\n", encoding="utf-8")
    return repo


def test_worker_tick_tests_committen_nicht_im_repo_in_dem_sie_laufen(tmp_path):
    repo = _wegwerf_repo(tmp_path)
    kopf = _git(repo, "rev-parse", "HEAD").strip()

    res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "tests/test_worker_tick.py"],
                         cwd=repo, env=_env(), capture_output=True, text=True, timeout=600)

    assert res.returncode == 0, res.stdout[-3000:]
    assert _git(repo, "rev-parse", "HEAD").strip() == kopf, _git(repo, "log", "--oneline", "-3")
    assert _git(repo, "status", "--porcelain").splitlines() == ["?? uncommittet.txt"]
