#!/usr/bin/env python3
"""Housekeeping der Agenten-Sandbox: Worktrees, gemergte Branches und alte Belegungen (#225).

Ein systemd-Timer (`agent-gc.timer`, alle 6 Stunden) startet dieses Skript auf CT 112. Es räumt
auf, was der Dauerbetrieb ansammelt: administrative Reste verwaister Git-Worktrees, lokale
Branches, die längst in `main` eingegangen sind, und `.done`-/`.claim`-Dateien in
`/var/lib/worker-claims`.

Die Kernfunktionen bekommen den Git-Aufruf als `run` herein (wie `sandbox/core/git_ops.py`),
damit die Tests ohne echtes Repository und ohne Server laufen. Branches werden nur lokal
gelöscht (kein Push, kein Remote). `cleanup_merged_branches` nutzt `git branch -d`;
`cleanup_squashed_branches` prüft inhaltlich, ob die Änderungen eines Branches vollständig in
`origin/main` stehen, und löscht nur dann mit `git branch -D`.
"""
from __future__ import annotations

import json
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
STREU_ORTE: tuple[str, ...] = ("/tmp", "/home/worker")
STREU_MIN_ALTER_S: int = 86400
STREU_MARKE: str = os.path.join(CLAIM_DIR, "gc-streukopien.json")


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


def cleanup_squashed_branches(
    repo_dir: str | Path,
    main_branch: str = "main",
    run: Runner | None = None,
) -> list[str]:
    """Löscht lokale Branches, deren Arbeit per Squash-Merge in `origin/<main_branch>` steht.

    Git hält einen squash-gemergten Branch nicht für gemergt, weil die Commits neu geschrieben
    wurden. Deshalb wird der Inhalt verglichen: Alle Dateien, die der Branch seit seiner
    Abzweigung geändert hat, müssen in `origin/<main_branch>` denselben Inhalt haben. Hat `main`
    eine davon später weiter geändert, bleibt der Branch stehen (sichere Seite). Branches, die in
    einem Worktree ausgecheckt sind, und `main`/`master`/`main_branch` sind ausgenommen. Gelöscht
    wird nur lokal mit `git branch -D`. Gibt die erfolgreich gelöschten Namen in der Reihenfolge
    von `for-each-ref` zurück und wirft nie (OSError, SubprocessError)."""
    runner = run or _run
    repo = str(repo_dir)
    try:
        fetch = runner(["git", "-C", repo, "fetch", "--quiet", "origin", main_branch])
    except (OSError, subprocess.SubprocessError):
        return []
    if fetch.returncode != 0:
        return []

    try:
        refs = runner(["git", "-C", repo, "for-each-ref", "--format=%(refname:short)", "refs/heads"])
        if refs.returncode != 0:
            return []
        worktrees = runner(["git", "-C", repo, "worktree", "list", "--porcelain"])
        if worktrees.returncode != 0:
            return []
    except (OSError, subprocess.SubprocessError):
        return []

    geschuetzt = GESCHUETZTE_BRANCHES | {main_branch}
    aktiv: set[str] = set()
    for zeile in (worktrees.stdout or "").splitlines():
        if zeile.startswith("branch "):
            ref = zeile[len("branch "):].strip()
            if ref.startswith("refs/heads/"):
                aktiv.add(ref[len("refs/heads/"):])

    ziel = f"origin/{main_branch}"
    geloescht: list[str] = []
    for name in (refs.stdout or "").splitlines():
        name = name.strip()
        if not name or name in geschuetzt or name in aktiv:
            continue
        try:
            basis_ergebnis = runner(["git", "-C", repo, "merge-base", name, ziel])
            if basis_ergebnis.returncode != 0:
                continue
            basis = (basis_ergebnis.stdout or "").strip()
            diff_ergebnis = runner(["git", "-C", repo, "diff", "--name-only", basis, name])
            if diff_ergebnis.returncode != 0:
                continue
            dateien = [zeile for zeile in (diff_ergebnis.stdout or "").split("\n") if zeile]
            if not dateien:
                continue
            pruefung = runner(["git", "-C", repo, "diff", "--quiet", name, ziel, "--", *dateien])
            if pruefung.returncode != 0:
                continue
            loeschen = runner(["git", "-C", repo, "branch", "-D", name])
        except (OSError, subprocess.SubprocessError):
            continue
        if loeschen.returncode == 0:
            geloescht.append(name)
    return geloescht


def streukopien(
    orte: Sequence[str] = STREU_ORTE,
    bekannt: Sequence[str] = (),
    min_alter_s: float = STREU_MIN_ALTER_S,
    now: float | None = None,
) -> list[str]:
    """Findet Git-Klone (Tiefe 1) außerhalb der bekannten Arbeitsordner.

    Ein Streuklon ist ein Verzeichnis direkt unter einem Ort mit einem Eintrag `.git` (Datei oder
    Ordner), dessen mtime älter als `now - min_alter_s` ist und das weder in `bekannt` steht noch
    unter einem Pfad aus `bekannt` liegt. Fehlende oder unlesbare Orte zählen nicht. Gibt
    sortierte absolute Pfade zurück, wirft nie und löscht nie."""
    referenz = time.time() if now is None else now
    grenze = referenz - min_alter_s
    bekannt_abs = [os.path.abspath(pfad) for pfad in bekannt]
    treffer: list[str] = []
    for ort in orte:
        try:
            eintraege = list(os.scandir(ort))
        except OSError:
            continue
        for eintrag in eintraege:
            try:
                if not eintrag.is_dir():
                    continue
                pfad = os.path.abspath(eintrag.path)
                if not os.path.exists(os.path.join(pfad, ".git")):
                    continue
                if eintrag.stat().st_mtime >= grenze:
                    continue
                if any(pfad == alt or pfad.startswith(alt + os.sep) for alt in bekannt_abs):
                    continue
            except OSError:
                continue
            treffer.append(pfad)
    return sorted(treffer)


def neue_streukopien(treffer: Sequence[str], marke: str = STREU_MARKE) -> list[str]:
    """Meldet jeden Streuklon einmal und merkt sich den aktuellen Stand in `marke`.

    Zurück kommen die Pfade aus `treffer`, die noch nicht in der JSON-Liste `marke` stehen, in
    der Reihenfolge von `treffer`. Danach wird genau `sorted(treffer)` gespeichert; verschwundene
    Pfade fallen heraus und gelten bei Rückkehr wieder als neu. Eine fehlende oder kaputte Marke
    zählt als leer; ein Schreibfehler wirft nicht."""
    try:
        with open(marke, encoding="utf-8") as fh:
            bekannt = json.load(fh)
        if not isinstance(bekannt, list):
            bekannt = []
    except (OSError, ValueError):
        bekannt = []
    bekannt_set = set(bekannt)
    neu = [pfad for pfad in treffer if pfad not in bekannt_set]
    try:
        with open(marke, "w", encoding="utf-8") as fh:
            json.dump(sorted(treffer), fh)
    except OSError:
        pass
    return neu


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
    2. `prune_worktrees`, `cleanup_merged_branches` und `cleanup_squashed_branches` in allen
       `project_dirs`

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
        geloescht += cleanup_squashed_branches(pfad, run=run)
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
    bekannt = [p.workdir for p in liste] + ["/root/work", "/root/sc-digger-worktrees"]
    neu = neue_streukopien(streukopien(bekannt=bekannt), STREU_MARKE)
    if neu:
        zeile = ("⚠ Stephan: Streukopien außerhalb der Arbeitsordner (nicht gelöscht): "
                 f"{', '.join(neu[:5])}")
        if len(neu) > 5:
            zeile += f" (+{len(neu) - 5} weitere)"
        print(zeile, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
