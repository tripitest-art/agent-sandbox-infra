"""Tests für sandbox/install-lib.sh (gemerkte Einstellungen, Projekte klonen) ohne Server und Netzwerk."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

if not (shutil.which("bash") and shutil.which("python3")):
    if os.environ.get("CI"):
        raise RuntimeError("bash und python3 fehlen, in CI Pflicht (Shell-Tests dürfen nicht entfallen)")
    pytest.skip("bash oder python3 fehlt auf diesem Rechner (Linux-Server: vorhanden)",
                allow_module_level=True)

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
    # systemctl-Fake: zeichnet die Aufrufe auf, damit die Timer-Logik ohne Server prüfbar ist.
    systemctl = bin_dir / "systemctl"
    systemctl.write_text('#!/bin/sh\necho "systemctl $*" >> "$SYSTEMCTL_LOG"\nexit 0\n')
    systemctl.chmod(systemctl.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "units").mkdir(exist_ok=True)
    (tmp_path / "timers.target.wants").mkdir(exist_ok=True)
    full = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "GIT_LOG": str(tmp_path / "git.log"),
            "AGENT_SANDBOX_CONF": str(tmp_path / "agent-sandbox.conf"), "SCRIPT_DIR": str(SANDBOX),
            "INFRA_DIR": str(tmp_path / "deploy-klon"), "SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
            "UNIT_DIR": str(tmp_path / "units"), "TIMER_DIR": str(tmp_path / "timers.target.wants"), **env}
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


def test_conf_save_keeps_keys_it_does_not_receive(tmp_path):
    """Ein Schlüssel, den install.sh nicht kennt, darf beim nächsten Lauf nicht verschwinden."""
    (tmp_path / "agent-sandbox.conf").write_text("HA_STATUS=an\nMAX_WORKERS=2\n")
    r = sh('MAX_WORKERS=3; REVIEW_POST=1; conf_save MAX_WORKERS REVIEW_POST\n'
           'echo "$(conf_get MAX_WORKERS)/$(conf_get REVIEW_POST)/$(conf_get HA_STATUS)"', tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "3/1/an"
    # Unbekannte Zeile in ursprünglicher Reihenfolge zuerst, danach die übergebenen Schlüssel je einmal.
    assert (tmp_path / "agent-sandbox.conf").read_text() == "HA_STATUS=an\nMAX_WORKERS=3\nREVIEW_POST=1\n"


def test_conf_save_sets_mode_600_on_an_existing_644_file(tmp_path):
    conf = tmp_path / "agent-sandbox.conf"
    conf.write_text("MAX_WORKERS=2\n")
    conf.chmod(0o644)
    r = sh('MAX_WORKERS=3; conf_save MAX_WORKERS\nstat -c %a "$AGENT_SANDBOX_CONF"', tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "600"


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


# ---- install.sh: MAX_WORKERS prüfen, Slot-Timer aus dem System abschalten ----

INSTALL_SOURCE = (SANDBOX / "install.sh").read_text()


def install_sh_slice(start, end):
    """Zeichengenauer Ausschnitt aus install.sh.

    install.sh selbst läuft nie (apt-get, systemctl, /etc; Regel 4 – das macht Stephan). Damit die
    neuen Bausteine trotzdem prüfbar sind, werden sie einzeln herausgeschnitten und mit dem
    systemctl-Fake oben gefahren. Fehlt ein Marker, bricht der Test ab, statt still nichts zu prüfen.
    """
    i = INSTALL_SOURCE.index(start)
    j = INSTALL_SOURCE.index(end, i)
    return INSTALL_SOURCE[i:j + len(end)]


def disable_slot_timers_fn():
    """Die Funktion aus install.sh, die jeden gefundenen Slot-Timer abschaltet."""
    return install_sh_slice("disable_slot_timers() {", "\n}\n")


def systemctl_log(tmp_path):
    f = tmp_path / "systemctl.log"
    return f.read_text().splitlines() if f.exists() else []


def slot_timers_present(tmp_path, *slots):
    d = tmp_path / "timers.target.wants"
    d.mkdir(parents=True, exist_ok=True)
    for i in slots:
        (d / f"qwen-worker@{i}.timer").touch()


def test_disable_slot_timers_shuts_down_every_slot_found_in_the_system(tmp_path):
    """Der Bereich kommt aus dem System, nicht aus 1–5: Slots 6 und 7 dürfen nicht aktiv bleiben."""
    slot_timers_present(tmp_path, *range(1, 8))
    fn = install_sh_slice("disable_slot_timers() {", "\n}\n")
    r = sh(f"{fn}\ndisable_slot_timers", tmp_path)
    assert r.returncode == 0, r.stderr
    assert systemctl_log(tmp_path) == [f"systemctl disable --now qwen-worker@{i}.timer"
                                      for i in range(1, 8)]


def test_multi_worker_branch_stops_slots_above_the_wanted_number(tmp_path):
    """Beim Verkleinern (hier 8 → 2) muss der Multi-Worker-Zweig die Slots 3–7 abschalten."""
    slot_timers_present(tmp_path, *range(1, 8))
    block = install_sh_slice(
        'if [ "$MAX_WORKERS" -gt 1 ]; then',
        'echo "Taktgeber: Multi-Worker mit $MAX_WORKERS Slots aktiv (qwen-worker@1..$MAX_WORKERS.timer)"\n')
    r = sh(f"MAX_WORKERS=2\nTAKT=5min\n{disable_slot_timers_fn()}\n{block}\nfi\n", tmp_path)
    assert r.returncode == 0, r.stderr
    log = systemctl_log(tmp_path)
    abgeschaltet = {l.split()[-1] for l in log if l.startswith("systemctl disable --now qwen-worker@")}
    assert {f"qwen-worker@{i}.timer" for i in range(3, 8)} <= abgeschaltet
    aktiviert = {l.split()[-1] for l in log if l.startswith("systemctl enable qwen-worker@")}
    assert aktiviert == {"qwen-worker@1.timer", "qwen-worker@2.timer"}
    assert "systemctl disable --now qwen-worker.timer" in log
    assert sorted(p.name for p in (tmp_path / "units").glob("qwen-worker@*.timer")) == \
        ["qwen-worker@1.timer", "qwen-worker@2.timer"]


def test_ungueltiger_max_workers_bricht_ab_und_merkt_nichts(tmp_path):
    """Ein gemerktes „zwei“ darf die Datei nicht erreichen: sonst scheitert jeder weitere Lauf."""
    (tmp_path / "agent-sandbox.conf").write_text("MAX_WORKERS=5\n")
    pruefung = install_sh_slice("if ! [[ $WORKERS_WANTED =~ ", "\nfi\n")
    r = sh(f"MAX_WORKERS=zwei\nWORKERS_WANTED=$MAX_WORKERS\n{pruefung}\nconf_save MAX_WORKERS", tmp_path)
    assert r.returncode != 0
    assert "MAX_WORKERS=zwei ist keine Zahl >= 1" in r.stderr
    assert "MAX_WORKERS=<N>" in r.stderr   # nennt den Weg aus einer schon gemerkten Sackgasse
    assert (tmp_path / "agent-sandbox.conf").read_text() == "MAX_WORKERS=5\n"


def test_gescheiterte_pruefung_schaltet_worker_und_review_timer_ab(tmp_path):
    """ok != 1: Die Meldung „Timer bleibt aus“ stimmt nur, wenn auch der Review-Takt aus ist."""
    block = install_sh_slice('if [ "$ok" = 1 ]; then', "\nfi\n")
    r = sh(f"ok=0\nMAX_WORKERS=1\nworker_timers=qwen-worker.timer\n"
           f"{disable_slot_timers_fn()}\n{block}", tmp_path)
    assert r.returncode == 0, r.stderr
    assert "systemctl disable --now qwen-worker.timer" in systemctl_log(tmp_path)
    assert "systemctl disable --now qwen-review.timer" in systemctl_log(tmp_path)
    assert "Timer bleibt aus" in r.stdout
