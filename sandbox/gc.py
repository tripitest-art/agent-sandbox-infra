#!/usr/bin/env python3
"""Housekeeping der Agenten-Sandbox: Worktrees, gemergte Branches und alte Belegungen (#225).

Ein systemd-Timer (`agent-gc.timer`, alle 6 Stunden) startet dieses Skript auf CT 112. Es räumt
auf, was der Dauerbetrieb ansammelt: administrative Reste verwaister Git-Worktrees, lokale
Branches, die längst in `main` eingegangen sind, und `.done`-/`.claim`-Dateien in
`/var/lib/worker-claims`.

Die Kernfunktionen bekommen den Git-Aufruf als `run` herein (wie `sandbox/core/git_ops.py`),
damit die Tests ohne echtes Repository und ohne Server laufen. Ungemeldete Branches werden nie
gelöscht: ausschließlich `git branch -d`, nie `-D`, und nur lokal (kein Push, kein Remote).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]

CLAIM_DIR = os.environ.get("CLAIM_DIR", "/var/lib/worker-claims")
CLAIM_MAX_AGE_SECONDS = 7 * 86400
GESCHUETZTE_BRANCHES = frozenset({"main", "master"})


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    """Vorgabe-Runner: führt den Befehl aus, ohne bei Fehlercode zu werfen."""
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def prune_worktrees(repo_dir: str | Path, run: Runner | None = None) -> bool:
    """Führt `git worktree prune` im angegebenen Repository `repo_dir` aus.

    Gibt True zurück, wenn returncode == 0, sonst False. Fängt FileNotFoundError und
    subprocess.SubprocessError ab (wirft nicht)."""
    runner = run or _run
    try:
        ergebnis = runner(["git", "-C", str(repo_dir), "worktree", "prune"])
    except (OSError, subprocess.SubprocessError):
        return False
    return ergebnis.returncode == 0


def cleanup_merged_branches(
    repo_dir: str | Path,
    main_branch: str = "main",
    run: Runner | None = None,
) -> list[str]:
    """Löscht lokale Branches, die bereits in `main_branch` eingegangen sind.

    `git branch --merged` markiert den aktiven Branch mit `*` (andere Worktrees mit `+`); solche
    Zeilen und die geschützten Namen `main`/`master`/`main_branch` bleiben stehen. Gelöscht wird
    nur mit `git branch -d`, also nur vollständig gemergte Branches. Gibt die Liste der
    erfolgreich gelöschten Namen zurück; ein Git-Fehler liefert eine leere Liste."""
    runner = run or _run
    try:
        ergebnis = runner(["git", "-C", str(repo_dir), "branch", "--merged", main_branch])
    except (OSError, subprocess.SubprocessError):
        return []
    if ergebnis.returncode != 0:
        return []

    geschuetzt = GESCHUETZTE_BRANCHES | {main_branch}
    geloescht: list[str] = []
    for zeile in (ergebnis.stdout or "").splitlines():
        if not zeile.strip():
            continue
        # "* main" = aktiver Branch, "+ branch" = in einem anderen Worktree ausgecheckt.
        if zeile.lstrip().startswith(("*", "+")):
            continue
        branch = zeile.strip()
        if not branch or branch in geschuetzt:
            continue
        try:
            loeschen = runner(["git", "-C", str(repo_dir), "branch", "-d", branch])
        except (OSError, subprocess.SubprocessError):
            continue
        if loeschen.returncode == 0:
            geloescht.append(branch)
    return geloescht


def prune_claims(
    claim_dir: str | Path,
    max_age_seconds: float = CLAIM_MAX_AGE_SECONDS,
    now: float | None = None,
) -> int:
    """Löscht alte `.done`- und verwaiste `.claim`-Dateien in `claim_dir`.

    Maßstab ist die mtime: älter als `now - max_age_seconds`. Andere Dateien, Unterverzeichnisse
    und junge Marken bleiben. Gibt die Anzahl gelöschter Dateien zurück und wirft keinen OSError
    (ein fehlendes oder nicht lesbares Verzeichnis ergibt 0)."""
    referenz = time.time() if now is None else now
    grenze = referenz - max_age_seconds
    anzahl = 0
    try:
        eintraege = list(os.scandir(claim_dir))
    except OSError:
        return 0
    for eintrag in eintraege:
        try:
            if not eintrag.is_file(follow_symlinks=False):
                continue
            if not eintrag.name.endswith((".done", ".claim")):
                continue
            if eintrag.stat(follow_symlinks=False).st_mtime >= grenze:
                continue
            os.unlink(eintrag.path)
            anzahl += 1
        except OSError:
            continue
    return anzahl


def run_gc(
    claim_dir: str | Path,
    project_dirs: Sequence[str | Path],
    run: Runner | None = None,
) -> dict[str, int]:
    """Führt den vollen Housekeeping-Durchlauf aus.

    1. `prune_claims` in `claim_dir`
    2. `prune_worktrees` und `cleanup_merged_branches` in allen `project_dirs`

    Gibt eine Statistik zurück: {'claims': N, 'branches': M, 'repos': K}. `repos` zählt nur
    vorhandene Projektverzeichnisse; ein fehlender Klon wird übersprungen, nicht als Fehler
    gewertet. Git-Fehler eines Repositories brechen den Durchlauf nicht ab."""
    statistik = {
        "claims": prune_claims(claim_dir),
        "branches": 0,
        "repos": 0,
    }
    for projekt in project_dirs:
        pfad = str(projekt)
        if not os.path.isdir(pfad):
            print(f"gc: {pfad} fehlt, übersprungen.", flush=True)
            continue
        if not prune_worktrees(pfad, run=run):
            print(f"gc: worktree prune in {pfad} fehlgeschlagen.", flush=True)
        geloescht = cleanup_merged_branches(pfad, run=run)
        if geloescht:
            print(f"gc: {pfad}: {len(geloescht)} gemergte Branches gelöscht "
                  f"({', '.join(geloescht)}).", flush=True)
        statistik["branches"] += len(geloescht)
        statistik["repos"] += 1
    return statistik


def main() -> int:
    """Einstieg für `agent-gc.service`: Projektliste lesen und einen Durchlauf ausführen."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # projects.py liegt daneben
    import projects  # noqa: E402

    try:
        liste = projects.load_projects()
    except projects.ProjectConfigError as fehler:
        # Regel 7: eine kaputte Projektliste bricht ab, statt still nichts zu tun.
        print(f"⚠ Stephan: Projektliste unbrauchbar, Housekeeping abgebrochen: {fehler}", flush=True)
        return 1
    stats = run_gc(CLAIM_DIR, [p.workdir for p in liste])
    print(f"gc: {stats['claims']} Belegungen, {stats['branches']} Branches, "
          f"{stats['repos']} Repositories aufgeräumt.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
