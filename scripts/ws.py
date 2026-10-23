"""Legt einen eigenen Worktree für ein Werkzeug der Planer-Umgebung (CT 106) an.

Warum es das gibt: Claude Code, AGY und OpenCode arbeiten auf demselben Dateisystem. Würden sie
im selben Klon arbeiten, schrieben sie sich in dieselben Dateien (AGENTS.md, Regeln 2 und 8).
Darum bleiben die Hauptklone unter `/config/projects` auf `main` und werden nur gepullt; jede
Aufgabe bekommt je Werkzeug einen Worktree unter `/config/work/<werkzeug>/<repo>-<kurz>` auf
dem Branch `feature/<kurz>`, abgezweigt von `origin/main`.

Aufruf: `python scripts/ws.py <claude|agy|opencode> <repo> <kurz>`

Zum reinen Planen (Issues entwerfen, Code lesen) braucht es keinen Worktree je Thema:
`python scripts/ws.py plan <claude|agy|opencode> <repo>` hält je Werkzeug und Repo einen festen
Planungs-Worktree `<repo>-planung` auf dem aktuellen `origin/main` bereit. Er hängt an keinem
Branch (detached), damit sich aus ihm nichts versehentlich pushen lässt; ändern soll man dort
nichts. Soll eine Datei im Repository geändert werden, bleibt es beim Aufruf oben.

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


PLAN_KURZ = "planung"


def _git_out(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def plan(tool: str, repo: str, projects: Path, work: Path) -> tuple[Path, str]:
    """Hält den Planungs-Worktree `<repo>-planung` auf `origin/main` bereit.

    Gibt (Pfad, Zustand) zurück; Zustand ist „angelegt“ oder „aktualisiert“. Der Worktree hängt an
    keinem Branch. Hat er lokale Änderungen oder steht auf einem Branch, wird er nicht angefasst
    und die Funktion meldet das als Fehler, damit nichts verloren geht.
    """
    validate(tool, repo, PLAN_KURZ)
    main_clone = projects / repo
    if not (main_clone / ".git").exists():
        raise WsError(f"Kein Klon unter {main_clone}")
    ziel = worktree_path(tool, repo, PLAN_KURZ, work)

    if ziel.exists():
        # Nur ein Worktree dieses Klons darf überschrieben werden, kein fremder Ordner gleichen Namens.
        gemeinsam = _git_out("-C", str(ziel), "rev-parse", "--path-format=absolute", "--git-common-dir")
        if gemeinsam.returncode != 0 or Path(gemeinsam.stdout.strip()).resolve() != (main_clone / ".git").resolve():
            raise WsError(f"{ziel} existiert, ist aber kein Worktree von {main_clone}")
        if _git_out("-C", str(ziel), "status", "--porcelain").stdout.strip():
            raise WsError(f"{ziel} hat lokale Änderungen und wird nicht aktualisiert (aufräumen oder verwerfen)")
        if _git_out("-C", str(ziel), "symbolic-ref", "-q", "HEAD").returncode == 0:
            raise WsError(f"{ziel} steht auf einem Branch; der Planungs-Worktree hängt an keinem Branch")

    fetch = subprocess.run(["git", "-C", str(main_clone), "fetch", "origin", "main"])
    if fetch.returncode != 0:
        raise WsError(f"git fetch origin main ist gescheitert (Exit {fetch.returncode}); Zugriff auf origin prüfen")

    if ziel.exists():
        sw = subprocess.run(["git", "-C", str(ziel), "checkout", "--detach", "origin/main"])
        if sw.returncode != 0:
            raise WsError(f"git checkout --detach origin/main ist gescheitert (Exit {sw.returncode})")
        return ziel, "aktualisiert"

    ziel.parent.mkdir(parents=True, exist_ok=True)
    add = subprocess.run(["git", "-C", str(main_clone), "worktree", "add", "--detach", str(ziel), "origin/main"])
    if add.returncode != 0:
        raise WsError(f"git worktree add ist gescheitert (Exit {add.returncode})")
    return ziel, "angelegt"


def _main_plan(argv: Sequence[str], env: Mapping[str, str]) -> int:
    parser = argparse.ArgumentParser(prog="ws plan", description="Hält den Planungs-Worktree auf origin/main bereit.")
    parser.add_argument("tool", choices=TOOLS, help="Werkzeug, das im Worktree plant")
    parser.add_argument("repo", help="Ordnername des Hauptklons unter WS_PROJECTS")
    args = parser.parse_args(argv)

    projects = Path(env.get("WS_PROJECTS", DEFAULT_PROJECTS))
    work = Path(env.get("WS_WORK", DEFAULT_WORK))
    try:
        ziel, zustand = plan(args.tool, args.repo, projects, work)
    except WsError as err:
        print(f"Fehler: {err}", file=sys.stderr)
        return 1
    print(f"Planungs-Worktree {zustand}: {ziel} (auf origin/main, ohne Branch)")
    print("Nur lesen und planen. Soll eine Datei im Repository geändert werden: ws <werkzeug> <repo> <kurz>.")
    print(f"Entwürfe gemeinsam ablegen unter: {work / 'entwuerfe'}")
    return 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "plan":
        return _main_plan(argv[1:], env)
    parser =argparse.ArgumentParser(prog="ws", description="Legt einen Worktree je Werkzeug und Aufgabe an.")
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
