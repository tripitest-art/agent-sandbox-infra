#!/usr/bin/env python3
"""Auto-Deploy der Sandbox-Infrastruktur (Stufe 3, Issue #15).

Ein systemd-Timer startet dieses Skript alle 15 Minuten (agent-sandbox-deploy.timer). Es holt
`origin/main`, prüft einen neuen Stand mit pytest, rollt ihn per `sandbox/install.sh` aus und
setzt bei einem Fehler auf den vorherigen Stand zurück. Der Merge nach `main` bleibt Stephans
Entscheidung (AGENTS.md Regel 4); dieses Skript holt nur, was dort schon liegt.

Kein Agent führt das aus: Es läuft als Dienst auf CT 112. Die Kernlogik (`deploy_once`) bekommt
Befehle und Benachrichtigung als Funktionen, damit die Tests ohne git, Netzwerk und Server laufen.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

STATE_FILE: str = ".deploy-state.json"
SLOT_DIR: str = os.environ.get("SLOT_DIR", "/run/worker-slots")
LOCK_DIR: str = os.environ.get("LOCK_DIR", "/run")
INFRA_DIR: str = os.environ.get("INFRA_DIR", "/root/agent-sandbox-infra")
# Ein Lauf darf lange dauern (install.sh macht apt-get); danach gilt er als hängend.
COMMAND_TIMEOUT: int = 3600

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


@dataclass
class DeployResult:
    """Ergebnis eines Auto-Deploy-Laufs."""
    status: str  # unveraendert | ausgerollt | zurueckgerollt | uebersprungen | fehler
    old: str | None  # vorheriger Commit (Kurz-Hash)
    new: str | None  # Ziel-Commit (Kurz-Hash)
    detail: str


def _lock_held(path: str) -> bool:
    """True, wenn ein anderer Prozess die flock-Sperre auf `path` hält.

    Die Lock-Dateien in /run bleiben nach einem Lauf bestehen; ihre bloße Existenz sagt nichts.
    Auf Systemen ohne fcntl (Windows, nur Entwicklung) gilt keine Sperre als gehalten."""
    try:
        import fcntl
    except ImportError:
        return False
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def is_worker_or_review_active(
    slot_dir: str = SLOT_DIR,
    lock_dir: str = LOCK_DIR,
) -> bool:
    """Prüft, ob ein Worker-Slot (Status 'running' in slot-*.json) oder ein Takt-Lock
    (/run/qwen-worker-w*.lock, /run/qwen-review.lock) aktiv ist."""
    for path in glob.glob(os.path.join(slot_dir, "slot-*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue  # halb geschrieben oder weg: das zählt nicht als aktiv
        if isinstance(data, dict) and data.get("status") == "running":
            return True
    locks = glob.glob(os.path.join(lock_dir, "qwen-worker-w*.lock"))
    locks.append(os.path.join(lock_dir, "qwen-review.lock"))
    return any(_lock_held(p) for p in locks)


def load_state(repo: Path) -> dict:
    """Liest .deploy-state.json atomar aus repo; liefert {} bei Fehler oder Fehlen."""
    try:
        data = json.loads((Path(repo) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(repo: Path, state: dict) -> None:
    """Speichert state atomar (.tmp + os.replace) nach repo/.deploy-state.json."""
    path = Path(repo) / STATE_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _out(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "").strip()


def deploy_once(
    repo: Path,
    run: Callable[[Sequence[str]], subprocess.CompletedProcess],
    notify: Callable[[str], None],
    *,
    is_busy: Callable[[], bool] = is_worker_or_review_active,
    test_cmd: Sequence[str] = ("python", "-m", "pytest", "-q"),
    install_cmd: Sequence[str] = ("bash", "sandbox/install.sh"),
    now: Callable[[], str] = lambda: time.strftime("%Y-%m-%dT%H:%M:%S"),
) -> DeployResult:
    """Führt einen Deploy-Prüfzyklus aus:
    1. is_busy() aktiv -> 'uebersprungen' (kein Eingriff in laufende Worker/Reviews).
    2. git status dirty -> 'uebersprungen', einmalig pro Ziel-Commit 'Lokale Änderungen' melden.
    3. git fetch origin main -> bei Fehler 'fehler'.
    4. Ziel-Commit ermitteln; ist new == old -> 'unveraendert'.
    5. Ist new in state['bad'] -> 'uebersprungen' (fehlerhafter Stand wird nicht wiederholt).
    6. git pull --ff-only origin main; Vorab-Test test_cmd ausführen.
       - Scheitert test_cmd: git reset --hard old, state['bad'] = new, '⚠ Stephan:'-Meldung,
         Rückgabe 'zurueckgerollt'.
    7. install_cmd ausführen.
       - Scheitert install_cmd: git reset --hard old + install_cmd (Rollback),
         state['bad'] = new, '⚠ Stephan:'-Meldung, Rückgabe 'zurueckgerollt'.
    8. Erfolg: state['last_good'] = new, state['last_good_at'] = now(), state['bad'] = None,
       Benachrichtigung, Rückgabe 'ausgerollt' (last_good_at fürs Dashboard).
    """
    repo = Path(repo)
    if is_busy():
        return DeployResult("uebersprungen", None, None,
                            "Worker oder Review aktiv, Deploy wartet")

    state = load_state(repo)

    def fehler(old: str | None, new: str | None, detail: str) -> DeployResult:
        # Derselbe Fehler (z. B. Netz weg) soll nicht alle 15 Minuten eine Meldung auslösen.
        if state.get("last_error") != detail:
            state["last_error"] = detail
            save_state(repo, state)
            notify(f"⚠ Stephan: Auto-Deploy: {detail}")
        return DeployResult("fehler", old, new, detail)

    # Nur verfolgte Dateien: .deploy-state.json und __pycache__ sind keine „lokalen Änderungen“.
    status = run(["git", "status", "--porcelain", "--untracked-files=no"])
    if status.returncode != 0:
        return fehler(None, None, f"git status fehlgeschlagen (Exit {status.returncode})")
    dirty = bool(_out(status))

    fetch = run(["git", "fetch", "--quiet", "origin", "main"])
    if fetch.returncode != 0:
        return fehler(None, None, f"git fetch fehlgeschlagen (Exit {fetch.returncode})")
    old_p = run(["git", "rev-parse", "--short", "HEAD"])
    new_p = run(["git", "rev-parse", "--short", "origin/main"])
    if old_p.returncode != 0 or new_p.returncode != 0 or not _out(old_p) or not _out(new_p):
        return fehler(None, None, "Commit nicht ermittelbar (git rev-parse)")
    old, new = _out(old_p), _out(new_p)

    if dirty:
        if state.get("dirty_notified") != new:
            state["dirty_notified"] = new
            save_state(repo, state)
            notify(f"⚠ Stephan: Lokale Änderungen im Infra-Klon, Deploy von {new} übersprungen")
        return DeployResult("uebersprungen", old, new,
                            "lokale Änderungen im Klon, nichts überschrieben")

    if new == old:
        return DeployResult("unveraendert", old, new, "kein neuer Stand")
    if new == state.get("bad"):
        return DeployResult("uebersprungen", old, new,
                            f"Stand {new} ist als fehlerhaft vermerkt, nicht wiederholt")

    pull = run(["git", "pull", "--ff-only", "origin", "main"])
    if pull.returncode != 0:
        return fehler(old, new, f"git pull --ff-only fehlgeschlagen (Exit {pull.returncode})")

    def verwerfen(detail: str) -> DeployResult:
        # Zurück auf den letzten laufenden Stand und diesen Commit nicht erneut versuchen.
        reset = run(["git", "reset", "--hard", old])
        state["bad"] = new
        state["last_error"] = None
        save_state(repo, state)
        if reset.returncode != 0:
            detail += f"; Rollback auf {old} scheiterte (Exit {reset.returncode})"
        return finish(detail)

    def finish(detail: str) -> DeployResult:
        notify(f"⚠ Stephan: Auto-Deploy {old} -> {new} zurückgerollt: {detail}")
        return DeployResult("zurueckgerollt", old, new, detail)

    tests = run(list(test_cmd))
    if tests.returncode != 0:
        return verwerfen(f"Tests fehlgeschlagen (Exit {tests.returncode})")

    install = run(list(install_cmd))
    if install.returncode != 0:
        detail = f"install.sh fehlgeschlagen (Exit {install.returncode})"
        # Der halb installierte Stand muss wieder zum alten Code passen.
        run(["git", "reset", "--hard", old])
        again = run(list(install_cmd))
        if again.returncode != 0:
            detail += f"; erneutes install.sh auf {old} ebenfalls (Exit {again.returncode})"
        state["bad"] = new
        state["last_error"] = None
        save_state(repo, state)
        return finish(detail)

    state["last_good"] = new
    state["last_good_at"] = now()
    state["bad"] = None
    state["last_error"] = None
    state["dirty_notified"] = None
    save_state(repo, state)
    notify(f"Auto-Deploy: {old} -> {new} ausgerollt")
    return DeployResult("ausgerollt", old, new, f"{old} -> {new} ausgerollt")


def _log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [deploy] {msg}", flush=True)


def main() -> int:
    repo = Path(INFRA_DIR)

    def run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
        # Eine hängende Ausgabe darf den Takt nicht ewig blockieren (Regel 7).
        try:
            return subprocess.run(list(cmd), cwd=repo, capture_output=True, text=True,
                                  errors="replace", timeout=COMMAND_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(list(cmd), 124, "", type(exc).__name__)

    # Die Tests der Projekte (5 Minuten für sc-digger) sind hier unnötig: install.sh zeigt sie nur an.
    os.environ["SKIP_TESTS"] = "1"
    venv_python = "/root/venv/bin/python"
    python = venv_python if os.path.exists(venv_python) else sys.executable
    result = deploy_once(repo, run, _log, test_cmd=(python, "-m", "pytest", "-q"))
    if result.status != "unveraendert":
        _log(f"{result.status}: {result.detail}")
    return 1 if result.status == "fehler" else 0


if __name__ == "__main__":
    sys.exit(main())
