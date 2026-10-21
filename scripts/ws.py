"""Legt einen eigenen Worktree für ein Werkzeug der Planer-Umgebung (CT 106) an.

Warum es das gibt: Claude Code, AGY und OpenCode arbeiten auf demselben Dateisystem. Würden sie
im selben Klon arbeiten, schrieben sie sich in dieselben Dateien (AGENTS.md, Regeln 2 und 8).
Darum bleiben die Hauptklone unter `/config/projects` auf `main` und werden nur gepullt; jede
Aufgabe bekommt je Werkzeug einen Worktree unter `/config/work/<werkzeug>/<repo>-<kurz>` auf
dem Branch `feature/<kurz>`, abgezweigt von `origin/main`.

Aufruf: `python scripts/ws.py <claude|agy|opencode> <repo> <kurz>`
Die Ordner lassen sich über `WS_PROJECTS` und `WS_WORK` verschieben (Vorgabe wie oben).

Nur Standardbibliothek; `git` läuft als Unterprozess. Die Fehlermeldung von `git` erscheint
unverändert im Terminal und wird hier nicht weitergegeben (eine Remote-Adresse kann
Zugangsdaten enthalten, Regel 5).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

TOOLS = ("claude", "agy", "opencode")
DEFAULT_PROJECTS = "/config/projects"
DEFAULT_WORK = "/config/work"

# Nur Kleinbuchstaben, Ziffern und Bindestrich, nie am Anfang: der Name wird Teil von Branch und Pfad.
_KURZ_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Ein Verzeichnisname ohne Pfadanteil: kein "/", kein "..", kein führender Punkt.
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class WsError(Exception):
    """Eingabe oder Lage lässt keinen Worktree zu; die Meldung ist für Menschen gedacht."""


def validate(tool: str, repo: str, kurz: str) -> None:
    """Weist Werkzeug, Repo-Name und Kurzname ab, die einen falschen Pfad oder Branch ergäben."""
    if tool not in TOOLS:
        raise WsError(f"Unbekanntes Werkzeug: {tool} (erlaubt: {', '.join(TOOLS)})")
    if not _REPO_RE.match(repo) or ".." in repo:
        raise WsError(f"Repo-Name nur als einfacher Ordnername, ohne Pfadanteil: {repo!r}")
    if not _KURZ_RE.match(kurz):
        raise WsError(f"Kurzname nur aus a-z, 0-9 und Bindestrich, nicht am Anfang: {kurz!r}")


def worktree_path(tool: str, repo: str, kurz: str, work: Path) -> Path:
    return work / tool / f"{repo}-{kurz}"


def create(tool: str, repo: str, kurz: str, projects: Path, work: Path) -> Path:
    """Legt den Worktree an und gibt seinen Pfad zurück.

    Holt zuerst `origin/main`, damit der neue Branch vom aktuellen Stand ausgeht.
    """
    validate(tool, repo, kurz)
    main_clone = projects / repo
    if not (main_clone / ".git").exists():
        raise WsError(f"Kein Klon unter {main_clone}")
    ziel = worktree_path(tool, repo, kurz, work)
    if ziel.exists():
        raise WsError(f"Existiert schon: {ziel}")

    fetch = subprocess.run(["git", "-C", str(main_clone), "fetch", "origin", "main"])
    if fetch.returncode != 0:
        raise WsError(f"git fetch origin main ist gescheitert (Exit {fetch.returncode}); Zugriff auf origin prüfen")

    ziel.parent.mkdir(parents=True, exist_ok=True)
    # --no-track: Der Branch geht von origin/main aus, soll ihm aber nicht folgen; sonst wäre
    # origin/main sein Upstream und ein `git push` ohne Ziel würde dorthin zielen.
    add = subprocess.run(
        ["git", "-C", str(main_clone), "worktree", "add", "--no-track", str(ziel), "-b", f"feature/{kurz}", "origin/main"]
    )
    if add.returncode != 0:
        raise WsError(f"git worktree add ist gescheitert (Exit {add.returncode}); gibt es feature/{kurz} schon?")
    return ziel


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="ws", description="Legt einen Worktree je Werkzeug und Aufgabe an.")
    parser.add_argument("tool", choices=TOOLS, help="Werkzeug, das im Worktree arbeitet")
    parser.add_argument("repo", help="Ordnername des Hauptklons unter WS_PROJECTS")
    parser.add_argument("kurz", help="Kurzname der Aufgabe; wird zum Branch feature/<kurz>")
    args = parser.parse_args(argv)

    projects = Path(env.get("WS_PROJECTS", DEFAULT_PROJECTS))
    work = Path(env.get("WS_WORK", DEFAULT_WORK))
    try:
        ziel = create(args.tool, args.repo, args.kurz, projects, work)
    except WsError as err:
        print(f"Fehler: {err}", file=sys.stderr)
        return 1
    print(f"Worktree: {ziel} (Branch feature/{args.kurz})")
    print(f"Entwürfe gemeinsam ablegen unter: {work / 'entwuerfe'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
