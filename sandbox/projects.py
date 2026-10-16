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
    required_checks: tuple[str, ...] = ("tests", "acceptance-guard")   # Pflicht-Checks des Review-Takts
    manual_paths: tuple[str, ...] = ()          # fnmatch-Muster; ein Treffer = Merge immer durch Stephan


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
    known = {"name", "repo", "workdir", "test_cmd", "hotspot", "hotspot_label", "review_skill",
             "auto_merge", "required_checks", "manual_paths"}
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
    if "required_checks" in entry:
        checks = entry["required_checks"]
        # Ohne Einträge gäbe es überhaupt keine Pflicht-Checks mehr: ein roter Testlauf bliebe
        # dann folgenlos, weil checks_summary nur Namen aus dieser Liste bewertet.
        if (not isinstance(checks, list) or not checks
                or not all(isinstance(c, str) and c for c in checks)):
            raise ProjectConfigError(f"{where}: „required_checks“ muss eine nicht-leere Liste nicht-leerer Strings sein")
        entry = {**entry, "required_checks": tuple(checks)}   # Liste aus JSON -> Tupel wie im Feld
    if "manual_paths" in entry:
        paths = entry["manual_paths"]
        # Andernfalls ließe sich der manuelle Merge still abschalten (leerer String, Zahl, Text):
        # ein Projekt ohne Muster ist erlaubt, ein Projekt mit unbrauchbarem Muster nicht.
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            raise ProjectConfigError(f"{where}: „manual_paths“ muss eine Liste nicht-leerer Strings sein")
        entry = {**entry, "manual_paths": tuple(paths)}   # Liste aus JSON -> Tupel wie im Feld
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


def format_projects(items: list[Project]) -> str:
    """Eine Zeile je Projekt in Listenreihenfolge, verbunden mit einem Zeilenumbruch (kein Zeilenende am Schluss).

    Format: "<name> (<repo>): Merge <automatisch|manuell>, <Hotspot <hotspot>|kein Hotspot>".
    Eine leere Liste ergibt einen leeren String.
    """
    lines = []
    for p in items:
        merge = "automatisch" if p.auto_merge else "manuell"
        hotspot = f"Hotspot {p.hotspot}" if p.hotspot else "kein Hotspot"
        lines.append(f"{p.name} ({p.repo}): Merge {merge}, {hotspot}")
    return "\n".join(lines)
