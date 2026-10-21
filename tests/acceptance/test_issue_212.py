import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_hook_env", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_hook_env"] = w
_spec.loader.exec_module(w)

GIT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH")


def git(cwd, *args, env=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
                          cwd=str(cwd), check=True, capture_output=True, text=True, env=env)


def test_hook_setzt_die_git_umgebung_vor_dem_testbefehl_zurueck():
    h = w.pre_push_hook("python -m pytest -q")
    assert "unset" in h
    for name in GIT_VARS:
        assert name in h, name
    assert h.index("unset") < h.index("python -m pytest -q")
    assert "exit 1" in h
    assert "--no-verify" not in h


def test_hook_reicht_git_dir_nicht_an_den_testbefehl_weiter(tmp_path):
    if os.name == "nt":
        return   # der Hook ist ein sh-Skript; getestet wird auf dem Server und in CI
    ausgabe = tmp_path / "git_dir.txt"
    w.install_pre_push_hook(str(tmp_path / "hooks"), f'printf %s "${{GIT_DIR-leer}}" > {ausgabe}')
    env = dict(os.environ, GIT_DIR="/irgendwo/.git", GIT_WORK_TREE="/irgendwo")
    res = subprocess.run(["sh", str(tmp_path / "hooks" / "pre-push")], env=env,
                         capture_output=True, text=True)
    assert res.returncode == 0
    assert ausgabe.read_text(encoding="utf-8") == "leer"


def test_hook_blockiert_weiter_bei_rotem_testbefehl(tmp_path):
    if os.name == "nt":
        return
    w.install_pre_push_hook(str(tmp_path / "hooks"), "false")
    res = subprocess.run(["sh", str(tmp_path / "hooks" / "pre-push")], capture_output=True, text=True)
    assert res.returncode == 1


def test_echter_git_push_aus_einem_worktree_ruft_den_testbefehl_ohne_git_dir_auf(tmp_path):
    """Die Slots arbeiten in Worktrees; dort setzt git im Hook GIT_DIR auf .git/worktrees/<name>."""
    if os.name == "nt":
        return
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    klon = tmp_path / "klon"
    git(tmp_path, "clone", "-q", str(origin), str(klon))
    (klon / "a.txt").write_text("a\n", encoding="utf-8")
    git(klon, "add", "a.txt")
    git(klon, "commit", "-q", "-m", "Start")
    git(klon, "push", "-q", "origin", "HEAD:refs/heads/main")
    arbeit = tmp_path / "w1"
    git(klon, "worktree", "add", "-q", str(arbeit), "-b", "feature/x")
    (arbeit / "b.txt").write_text("b\n", encoding="utf-8")
    git(arbeit, "add", "b.txt")
    git(arbeit, "commit", "-q", "-m", "Zweiter")
    ausgabe = tmp_path / "git_dir.txt"
    w.install_pre_push_hook(str(klon / ".git" / "hooks"), f'printf %s "${{GIT_DIR-leer}}" > {ausgabe}')
    git(arbeit, "push", "-q", "origin", "feature/x")
    assert ausgabe.read_text(encoding="utf-8") == "leer"
