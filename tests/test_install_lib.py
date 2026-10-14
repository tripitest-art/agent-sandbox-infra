"""Tests für sandbox/install-lib.sh (gemerkte Einstellungen, Projekte klonen) ohne Server und Netzwerk."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"


def sh(script, tmp_path, **env):
    """Führt `script` mit geladener install-lib.sh aus. git ist ein Fake, der Aufrufe aufzeichnet."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    git = bin_dir / "git"
    git.write_text('#!/bin/sh\necho "git $*" >> "$GIT_LOG"\n'
                   '[ "$1" = clone ] && [ "$FAKE_CLONE_FAIL" = 1 ] && exit 128\n'
                   '[ "$3" = fetch ] && [ "$FAKE_FETCH_FAIL" = 1 ] && exit 128\n'
                   '[ "$3" = status ] && [ "$FAKE_DIRTY" = 1 ] && echo " M datei"\n'
                   '[ "$1" = clone ] && mkdir -p "$4/.git"\nexit 0\n')
    git.chmod(git.stat().st_mode | stat.S_IEXEC)
    full = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "GIT_LOG": str(tmp_path / "git.log"),
            "AGENT_SANDBOX_CONF": str(tmp_path / "agent-sandbox.conf"), "SCRIPT_DIR": str(SANDBOX),
            "INFRA_DIR": str(tmp_path / "deploy-klon"), **env}
    return subprocess.run(["bash", "-c", f"set -euo pipefail\n. {SANDBOX}/install-lib.sh\n{script}"],
                          capture_output=True, text=True, env=full)


def git_log(tmp_path):
    f = tmp_path / "git.log"
    return f.read_text().splitlines() if f.exists() else []


def projects_file(tmp_path, *entries):
    import json
    f = tmp_path / "projects.json"
    f.write_text(json.dumps({"projects": [
        {"name": n, "repo": r, "workdir": str(tmp_path / w)} for n, r, w in entries]}))
    return str(f)


# ---- gemerkte Einstellungen ----

def test_conf_roundtrip(tmp_path):
    r = sh('MAX_WORKERS=3; REVIEW_POST=1; REVIEW_AUTO_MERGE=0; conf_save MAX_WORKERS REVIEW_POST REVIEW_AUTO_MERGE\n'
           'echo "$(conf_get MAX_WORKERS)/$(conf_get REVIEW_POST)/$(conf_get REVIEW_AUTO_MERGE)"', tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "3/1/0"
    assert (tmp_path / "agent-sandbox.conf").read_text() == "MAX_WORKERS=3\nREVIEW_POST=1\nREVIEW_AUTO_MERGE=0\n"


def test_conf_get_without_file_is_empty_and_not_an_error(tmp_path):
    r = sh('v=$(conf_get MAX_WORKERS); echo "[$v]"', tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == "[]"


def test_conf_get_ignores_unknown_and_unsafe_lines(tmp_path):
    (tmp_path / "agent-sandbox.conf").write_text("MAX_WORKERS=$(rm -rf /)\nREVIEW_POST=1\n")
    r = sh('echo "[$(conf_get MAX_WORKERS)][$(conf_get REVIEW_POST)]"', tmp_path)
    assert r.stdout.strip() == "[][1]"


def test_conf_save_rejects_unsafe_value_and_keeps_old_file(tmp_path):
    (tmp_path / "agent-sandbox.conf").write_text("MAX_WORKERS=2\n")
    r = sh('MAX_WORKERS="2; reboot"; conf_save MAX_WORKERS', tmp_path)
    assert r.returncode != 0 and "unzulässige" in r.stderr
    assert (tmp_path / "agent-sandbox.conf").read_text() == "MAX_WORKERS=2\n"


# ---- Projekte ----

def test_ensure_projects_clones_missing_and_skips_existing(tmp_path):
    (tmp_path / "vorhanden" / ".git").mkdir(parents=True)
    pf = projects_file(tmp_path, ("a", "tripitest-art/a", "vorhanden"), ("b", "tripitest-art/b", "neu/b"))
    r = sh("ensure_projects", tmp_path, AGENT_PROJECTS=pf)
    assert r.returncode == 0, r.stderr
    clones = [l for l in git_log(tmp_path) if l.startswith("git clone")]
    assert clones == [f"git clone -q https://github.com/tripitest-art/b.git {tmp_path}/neu/b"]
    assert "Projekt a: Klon vorhanden" in r.stdout and "Projekt b: tripitest-art/b" in r.stdout


def test_ensure_projects_default_is_sc_digger(tmp_path):
    r = sh("project_lines", tmp_path, AGENT_PROJECTS=str(tmp_path / "gibt-es-nicht.json"))
    assert r.stdout.rstrip("\n").split("\t") == ["sc-digger", "tripitest-art/sc-digger", "/root/sc-digger"]


def test_ensure_projects_makes_worktree_dirs_only_for_several_workers(tmp_path):
    pf = projects_file(tmp_path, ("b", "tripitest-art/b", "neu/b"))
    sh("ensure_projects", tmp_path, AGENT_PROJECTS=pf, MAX_WORKERS="1")
    assert not (tmp_path / "neu" / "b-worktrees").exists()
    sh("ensure_projects", tmp_path, AGENT_PROJECTS=pf, MAX_WORKERS="2")
    assert (tmp_path / "neu" / "b-worktrees").is_dir()


def test_ensure_projects_refuses_deploy_checkout(tmp_path):
    pf = projects_file(tmp_path, ("infra", "tripitest-art/agent-sandbox-infra", "deploy-klon"))
    r = sh("ensure_projects", tmp_path, AGENT_PROJECTS=pf)
    assert r.returncode != 0 and "Deploy-Klon" in r.stderr
    assert git_log(tmp_path) == []


def test_ensure_projects_aborts_on_broken_list(tmp_path):
    f = tmp_path / "projects.json"
    f.write_text("{kaputt")
    r = sh("ensure_projects; echo nicht-erreicht", tmp_path, AGENT_PROJECTS=str(f))
    assert r.returncode != 0 and "Projektliste unbrauchbar" in r.stderr and "nicht-erreicht" not in r.stdout


def test_ensure_projects_aborts_when_clone_fails(tmp_path):
    pf = projects_file(tmp_path, ("b", "tripitest-art/b", "neu/b"))
    r = sh("ensure_projects", tmp_path, AGENT_PROJECTS=pf, FAKE_CLONE_FAIL="1")
    assert r.returncode != 0 and "Klonen von tripitest-art/b fehlgeschlagen" in r.stderr


# ---- install.sh ----

def test_install_scripts_are_valid_bash():
    for name in ("install.sh", "install-lib.sh"):
        assert subprocess.run(["bash", "-n", str(SANDBOX / name)]).returncode == 0


def test_install_wires_lib_and_survives_red_tests():
    text = (SANDBOX / "install.sh").read_text()
    assert '. "$SCRIPT_DIR/install-lib.sh"' in text
    assert "ensure_projects" in text and "conf_save" in text
    # die Testzeile darf set -e nicht auslösen
    line = next(l for l in text.splitlines() if "pytest -q 2>&1 | tail -1" in l)
    assert "||" in line


# ---- Basis-Klon auf main halten ----

def existing_clone(tmp_path):
    pf = projects_file(tmp_path, ("a", "o/a", "a-dir"))
    (tmp_path / "a-dir" / ".git").mkdir(parents=True)
    return pf


def test_existing_clone_is_detached_at_origin_main(tmp_path):
    pf = existing_clone(tmp_path)
    r = sh("ensure_projects", tmp_path, AGENT_PROJECTS=pf)
    assert r.returncode == 0, r.stderr
    log = git_log(tmp_path)
    d = str(tmp_path / "a-dir")
    assert f"git -C {d} fetch -q origin main" in log
    assert f"git -C {d} checkout -q --detach origin/main" in log
    # Der Branch main darf nicht belegt werden: Die Worker-Worktrees brauchen ihn (Regression 14.10.)
    assert not any(" -B main" in l or l.endswith("checkout -q main") for l in log)
    assert not any(l.startswith(("git clone", "git reset")) for l in log)


def test_dirty_clone_is_left_alone_with_warning(tmp_path):
    pf = existing_clone(tmp_path)
    r = sh("ensure_projects", tmp_path, AGENT_PROJECTS=pf, FAKE_DIRTY="1")
    assert r.returncode == 0
    assert not any("checkout" in l for l in git_log(tmp_path))
    assert "lokale Änderungen" in r.stderr


def test_fetch_failure_does_not_abort_install(tmp_path):
    pf = existing_clone(tmp_path)
    r = sh("ensure_projects", tmp_path, AGENT_PROJECTS=pf, FAKE_FETCH_FAIL="1")
    assert r.returncode == 0
    assert not any("checkout" in l for l in git_log(tmp_path))
    assert "fetch fehlgeschlagen" in r.stderr


def test_fresh_clone_is_not_synced_again(tmp_path):
    pf = projects_file(tmp_path, ("a", "o/a", "a-dir"))
    sh("ensure_projects", tmp_path, AGENT_PROJECTS=pf)
    assert not any("checkout" in l for l in git_log(tmp_path))
