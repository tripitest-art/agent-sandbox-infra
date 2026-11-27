#!/usr/bin/env python3
"""OpenCode-Läufe in einem eigenen Wegwerf-Klon statt im Worktree des Haupt-Repos.

Warum: Die git-Sperre in der OpenCode-Config ist ein Textmuster auf Shell-Segmente
(`git *`, `*git *`). Beliebiger Code kommt daran vorbei — `python3 -c "import subprocess;
subprocess.run(['git', …])"` oder ein Shell-Skript, das git aufruft. Ein durchgerutschter
Befehl soll deshalb nur den eigenen Klon treffen: `git clone --local --no-hardlinks` teilt
keine Objekt-Dateien, Refs und Worktrees mit dem Haupt-Repo. Der Ref-Schnappschuss vor und
nach dem Lauf belegt, dass die gemeinsamen Branches unberührt blieben.

Nebenwirkung des Musters `*git *`: es ist reiner Text, deshalb wird auch
`grep -r "git log" docs/` abgewiesen. Restrisiko beliebiger Code bleibt — genau deswegen
der Klon.

Das Skript läuft als `dev` mit `HOME=/config`; die Pfade sind Vorgaben für CT 106 und per
Option änderbar:

    python3 sandbox/opencode_run.py --repo home-infra --branch docs/probe \
        --prompt-file /tmp/auftrag.md

Exit-Codes: 0 in Ordnung, 1 Warnung (Haupt-Repo-Refs verändert), 2 Aufbaufehler,
3 OpenCode fehlgeschlagen. Nur Standardbibliothek.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]

PROJECTS_DIR = Path("/config/projects")
RUNS_DIR = Path("/config/work/opencode-runs")
LOGS_DIR = Path("/config/logs/opencode-runs")
DEFAULT_MODEL = "opencode/deepseek-v4.1-flash-free"
DEFAULT_TIMEOUT = 900
WARNUNG = "WARNUNG: Haupt-Repo-Refs verändert"
WEGWEIS = ("\n\nArbeite ausschließlich im aktuellen Arbeitsverzeichnis (Wegwerf-Klon). "
           "Das Haupt-Checkout {repo} ist tabu — dort nichts ändern. "
           "Verwende keine absoluten Pfade außerhalb des Klons.")


def auftrag_mit_hinweis(auftrag: str, haupt: str | Path) -> str:
    """Hängt den Wegweis an: cwd ist der Klon, absolute Pfade zeigen sonst ins Haupt-Checkout."""
    return auftrag.rstrip() + WEGWEIS.format(repo=haupt)


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    """Vorgabe-Runner: führt den Befehl aus, ohne bei Fehlercode zu werfen."""
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def ref_snapshot(repo: str | Path, run: Runner | None = None) -> str:
    """Refs, Worktrees und Stashes eines Repos als Text — die Vergleichsgrundlage."""
    runner = run or _run
    teile: list[str] = []
    for cmd in (
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname) %(objectname)"],
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        ["git", "-C", str(repo), "stash", "list"],
    ):
        kopf = " ".join(cmd[3:])
        try:
            ergebnis = runner(cmd)
        except (OSError, subprocess.SubprocessError) as fehler:
            teile.append(f"[{kopf}]\nFehler: {fehler}")
            continue
        teile.append(f"[{kopf}]\n{ergebnis.stdout or ''}")
    return "\n".join(teile)


def ref_diff(vorher: str, nachher: str) -> str:
    """Zeilenweise Differenz zweier Schnappschüsse; leer, wenn unverändert."""
    return "\n".join(
        difflib.unified_diff(
            vorher.splitlines(), nachher.splitlines(),
            fromfile="vorher", tofile="nachher", lineterm="",
        )
    )


def fetch_main(repo: str | Path, run: Runner | None = None) -> bool:
    """`git fetch` im Haupt-Repo; False, wenn kein Remote erreichbar ist."""
    runner = run or _run
    try:
        return runner(["git", "-C", str(repo), "fetch"]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def clone_local(
    source: str | Path, target: str | Path, branch: str, run: Runner | None = None
) -> bool:
    """Wegwerf-Klon ohne geteilte Objekte, danach Arbeitsbranch auf `origin/main`."""
    runner = run or _run
    try:
        if runner(["git", "clone", "--local", "--no-hardlinks",
                   str(source), str(target)]).returncode != 0:
            return False
        return runner(["git", "-C", str(target), "checkout", "-b", branch,
                       "origin/main"]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def export_patch(clone: str | Path, patch_path: str | Path, run: Runner | None = None) -> str:
    """Staged den Klon vollständig (nur das Skript, nie OpenCode) und schreibt den Patch.

    Gibt die `--stat`-Zeilen zurück, bei einem Fehler einen leeren String."""
    runner = run or _run
    try:
        if runner(["git", "-C", str(clone), "add", "-A"]).returncode != 0:
            return ""
        stat = runner(["git", "-C", str(clone), "diff", "--cached", "--stat"])
        diff = runner(["git", "-C", str(clone), "diff", "--cached", "--binary"])
    except (OSError, subprocess.SubprocessError):
        return ""
    if diff.returncode != 0:
        return ""
    Path(patch_path).write_text(diff.stdout or "", encoding="utf-8")
    return stat.stdout or ""


def summarize_log(path: str | Path) -> dict[str, int]:
    """Summiert Tokens aus den `step_finish`-Events und zählt Tool-Calls mit Status error."""
    summe = {
        "input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0,
        "schritte": 0, "tool_fehler": 0, "gesperrt": 0,
    }
    try:
        zeilen = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return summe
    for zeile in zeilen:
        try:
            ereignis = json.loads(zeile)
        except ValueError:
            continue                      # z. B. die abgebrochene letzte Zeile
        if not isinstance(ereignis, dict):
            continue
        teil = ereignis.get("part") or {}
        if ereignis.get("type") == "step_finish":
            summe["schritte"] += 1
            tokens = teil.get("tokens") or {}
            for feld in ("input", "output", "reasoning"):
                summe[feld] += int(tokens.get(feld) or 0)
            cache = tokens.get("cache") or {}
            summe["cache_read"] += int(cache.get("read") or 0)
            summe["cache_write"] += int(cache.get("write") or 0)
        elif ereignis.get("type") == "tool_use":
            zustand = teil.get("state") or {}
            if zustand.get("status") == "error":
                summe["tool_fehler"] += 1
                if "Permission denied" in str(zustand.get("error", "")):
                    summe["gesperrt"] += 1
    return summe


def limit_hinweis(path: str | Path, now: float | None = None) -> str:
    """Nennt ein Gratis-Limit (HTTP 429) aus dem Log samt Reset-Zeit; '' wenn keins vorkam."""
    try:
        zeilen = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for zeile in reversed(zeilen):
        try:
            ereignis = json.loads(zeile)
        except ValueError:
            continue
        if not isinstance(ereignis, dict) or ereignis.get("type") != "error":
            continue
        fehler = ereignis.get("error") or {}
        if fehler.get("status") != 429 and fehler.get("type") != "provider.free-limit":
            continue
        reset = ""
        millisekunden = fehler.get("retryAfterMs")
        if isinstance(millisekunden, (int, float)):
            basis = ereignis.get("timestamp")            # Reset ab Fehlerzeitpunkt, nicht ab jetzt
            if not isinstance(basis, (int, float)):
                basis = (time.time() if now is None else now) * 1000
            zeit = (basis + millisekunden) / 1000
            # CT 106 läuft in UTC; die Reset-Zeit ist in Freiburg gemeint.
            reset = (", Reset um " + datetime.fromtimestamp(zeit, ZoneInfo("Europe/Berlin"))
                     .strftime("%d.%m. %H:%M") + " (Europe/Berlin)")
        return ("Gratis-Limit erreicht (HTTP 429" + reset + "). Anderes Modell per --model, "
                "z. B. opencode-go/hy4-flash-free.")
    return ""


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenCode in einem Wegwerf-Klon laufen lassen und den Patch exportieren.")
    parser.add_argument("--repo", required=True, help="Verzeichnisname unter --projects-dir")
    parser.add_argument("--branch", required=True, help="neuer Branch im Klon")
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--projects-dir", type=Path, default=PROJECTS_DIR)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--logs-dir", type=Path, default=LOGS_DIR)
    parser.add_argument("--opencode", default=os.environ.get("OPENCODE_BIN", "opencode"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    haupt = args.projects_dir / args.repo
    if not (haupt / ".git").exists():
        print(f"Fehler: {haupt} ist kein Git-Repo.", file=sys.stderr)
        return 2
    auftrag = args.prompt_file.read_text(encoding="utf-8")

    # Erst fetch, dann Schnappschuss: sonst schlägt eine regulär geholte Remote-Ref als Warnung auf.
    if not fetch_main(haupt):
        print("Hinweis: git fetch fehlgeschlagen, lokaler Stand wird verwendet.", flush=True)
    vorher = ref_snapshot(haupt)

    name = f"{args.repo}-{time.strftime('%Y%m%d-%H%M%S')}"
    klon = args.runs_dir / name
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    if klon.exists():
        print(f"Fehler: {klon} existiert bereits.", file=sys.stderr)
        return 2
    if not clone_local(haupt, klon, args.branch):
        print(f"Fehler: Klon nach {klon} oder Branch {args.branch} fehlgeschlagen.",
              file=sys.stderr)
        return 2

    args.logs_dir.mkdir(parents=True, exist_ok=True)
    log = args.logs_dir / f"{name}.jsonl"
    umgebung = dict(os.environ, HOME="/config")
    umgebung["PATH"] = f"/config/.local/bin:{os.environ.get('PATH', '/usr/bin:/bin')}"
    # --standalone: eigener Server je Lauf. Ohne das hängt sich der Client an den
    # Hintergrunddienst, und dessen cwd (/config) gilt statt des Klons.
    befehl = [args.opencode, "run", "--standalone", auftrag_mit_hinweis(auftrag, haupt),
              "--model", args.model, "--format", "json"]
    start = time.monotonic()
    fehler = ""
    with log.open("w", encoding="utf-8") as protokoll:
        try:
            code = subprocess.run(befehl, cwd=klon, env=umgebung, stdout=protokoll,
                                  stderr=subprocess.STDOUT, timeout=args.timeout,
                                  check=False).returncode
        except subprocess.TimeoutExpired:
            code, fehler = -1, f"Zeitlimit {args.timeout}s überschritten"
        except OSError as ausnahme:
            code, fehler = -2, f"OpenCode nicht startbar: {ausnahme}"
    laufzeit = time.monotonic() - start

    patch = args.runs_dir / f"{name}.patch"
    stat = export_patch(klon, patch)
    nachher = ref_snapshot(haupt)
    diff = ref_diff(vorher, nachher)
    tokens = summarize_log(log)

    print(f"Klon:     {klon}")
    print(f"Patch:    {patch}")
    print(f"Log:      {log}")
    print(f"Branch:   {args.branch}")
    print(f"Laufzeit: {laufzeit:.0f}s, OpenCode-Exit: {code}"
          + (f" ({fehler})" if fehler else ""))
    print(f"Tokens:   {tokens['input']} input ({tokens['cache_read']} aus Cache), "
          f"{tokens['output']} output, {tokens['reasoning']} reasoning "
          f"in {tokens['schritte']} Schritten")
    print(f"Tool-Calls: {tokens['tool_fehler']} fehlgeschlagen, "
          f"davon {tokens['gesperrt']} von der git-Sperre abgewiesen")
    hinweis = limit_hinweis(log)
    if hinweis:
        print(hinweis)
    print("Dateien:")
    print(stat or "  (keine Änderung)")

    if diff:
        print(WARNUNG)
        print(diff)
        return 1
    if code != 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
