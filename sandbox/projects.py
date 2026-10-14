"""Projektliste für Worker- und Review-Takt (liegt neben den Skripten, install.sh kopiert sie mit).

Ein Projekt ist ein GitHub-Repository, in dem der Vertrag aus sc-digger gilt (AGENTS.md, Labels
`worker-task`/`bereit`/`agent-*`, Akzeptanztests unter tests/acceptance/, Check `acceptance-guard`).
Die Liste steht in /etc/agent-projects.json (Umgebung AGENT_PROJECTS überschreibt den Pfad), nie
im Repo: Sie nennt Pfade des Servers. Fehlt die Datei, läuft alles wie bisher mit sc-digger allein.

Beispiel: sandbox/projects.example.json
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

CONFIG_PATH = "/etc/agent-projects.json"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


class ProjectConfigError(ValueError):
    """Die Projektliste ist unbrauchbar. Der Takt bricht dann ab, statt ein Projekt still zu überspringen."""


@dataclass(frozen=True)
class Project:
    name: str                                   # Kurzname, Teil von Zustandsdateien und Log-Schlüsseln
    repo: str                                   # owner/name auf GitHub
    workdir: str                                # Klon auf dem Server (Basis für Worktrees)
    test_cmd: str = "python -m pytest -q"       # steht in den Aufträgen an den Worker
    hotspot: str | None = None                  # Datei, an der nie zwei Agenten zugleich arbeiten (Regel 8)
    hotspot_label: str = "berührt-main.py"      # Issue-Label, das den Hotspot anzeigt
    review_skill: str | None = None             # Pfad des Review-Skills im Repo, falls vorhanden
    auto_merge: bool = True                     # False: der Review-Takt mergt hier nie, Stephan immer


def default_projects() -> list[Project]:
    """Der Stand vor der Projektliste: sc-digger allein."""
    return [Project(name="sc-digger", repo="tripitest-art/sc-digger", workdir="/root/sc-digger",
                    hotspot="sc_digger/main.py", review_skill="skills/sc-digger-review/SKILL.md")]


def worktrees_dir(p: Project) -> str:
    return f"{p.workdir.rstrip('/')}-worktrees"


def _parse(entry: object, index: int) -> Project:
    where = f"Projekt {index + 1}"
    if not isinstance(entry, dict):
        raise ProjectConfigError(f"{where}: Objekt erwartet")
    known = {"name", "repo", "workdir", "test_cmd", "hotspot", "hotspot_label", "review_skill", "auto_merge"}
    unknown = set(entry) - known
    if unknown:
        raise ProjectConfigError(f"{where}: unbekannte Schlüssel {', '.join(sorted(unknown))}")
    for key in ("name", "repo", "workdir"):
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise ProjectConfigError(f"{where}: „{key}“ fehlt")
    if not _NAME_RE.match(entry["name"]):
        raise ProjectConfigError(f"{where}: „name“ nur aus a-z, 0-9, - und _ ({entry['name']!r})")
    if not _REPO_RE.match(entry["repo"]):
        raise ProjectConfigError(f"{where}: „repo“ muss owner/name sein ({entry['repo']!r})")
    if not entry["workdir"].startswith("/"):
        raise ProjectConfigError(f"{where}: „workdir“ muss ein absoluter Pfad sein")
    for key in ("test_cmd", "hotspot", "hotspot_label", "review_skill"):
        if key in entry and entry[key] is not None and (not isinstance(entry[key], str) or not entry[key]):
            raise ProjectConfigError(f"{where}: „{key}“ muss Text oder null sein")
    if "auto_merge" in entry and not isinstance(entry["auto_merge"], bool):
        raise ProjectConfigError(f"{where}: „auto_merge“ muss true oder false sein")
    kwargs = {k: v for k, v in entry.items() if k in known and not (k in ("test_cmd", "hotspot_label") and v is None)}
    return Project(**kwargs)


def parse_projects(text: str) -> list[Project]:
    """Liest den Inhalt der Projektliste. {"projects": [ … ]}; Namen und Repos je einmal."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProjectConfigError(f"kein gültiges JSON ({exc})") from exc
    entries = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ProjectConfigError("{\"projects\": [ … ]} mit mindestens einem Projekt erwartet")
    projects = [_parse(e, i) for i, e in enumerate(entries)]
    for field in ("name", "repo", "workdir"):
        values = [getattr(p, field) for p in projects]
        dup = sorted({v for v in values if values.count(v) > 1})
        if dup:
            raise ProjectConfigError(f"„{field}“ doppelt: {', '.join(dup)}")
    return projects


def load_projects(path: str | None = None, env: dict | None = None) -> list[Project]:
    """Projektliste vom Server; ohne Datei nur sc-digger. Ein Fehler in der Datei wird nicht abgefangen."""
    env = os.environ if env is None else env
    path = path or env.get("AGENT_PROJECTS") or CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return default_projects()
    try:
        return parse_projects(text)
    except ProjectConfigError as exc:
        raise ProjectConfigError(f"{path}: {exc}") from exc
