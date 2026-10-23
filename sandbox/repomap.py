"""Kompakte Symbol-Map (Repomap) der betroffenen Dateien für den Worker-Auftrag (#227).

Der Worker verbrennt bei der Orientierung viele Tokens mit rekursiven Suchbefehlen, bevor er
die erste Datei öffnet. Die Dateien stehen aber schon im Issue („Betroffene Dateien“). Dieses
Modul liefert daraus eine kurze Map aus Signaturen und Importzeilen — plus eine Ebene der
importierten Dateien. Es liest nur, schreibt nichts und ruft nichts auf (kein subprocess, kein
Netz), damit der Taktgeber daran nie blockieren kann.
"""

from __future__ import annotations

import ast
from pathlib import Path

ZEICHEN_PRO_TOKEN: int = 4    # grobe Herleitung: Modellwort ≈ 4 Zeichen


def _ist_datei(pfad: Path) -> bool:
    """`Path.is_file()` ohne Ausnahme, auch wenn ein Elternverzeichnis nicht lesbar ist."""
    try:
        return pfad.is_file()
    except OSError:
        return False


def _unter_root(repo_root: Path, pfad: Path) -> bool:
    """Wahr, wenn `pfad` aufgelöst unterhalb von `repo_root` liegt (kein Ausbruch per `..`)."""
    try:
        pfad.resolve().relative_to(repo_root.resolve())
    except (ValueError, OSError, RuntimeError):
        return False
    return True


def _relativ(repo_root: Path, pfad: Path) -> str | None:
    try:
        return pfad.resolve().relative_to(repo_root.resolve()).as_posix()
    except (ValueError, OSError, RuntimeError):
        return None


def _signatur(knoten: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """`def name(args)` beziehungsweise `async def name(args)` ohne Rumpf."""
    praefix = "async def" if isinstance(knoten, ast.AsyncFunctionDef) else "def"
    return f"{praefix} {knoten.name}({ast.unparse(knoten.args)})"


def _lies(pfad: Path) -> tuple[list[str], list[str]] | None:
    """Symbolzeilen und Importmodulnamen einer Datei; None bei Syntax- oder Lesefehlern.

    Fehlerhafte Dateien fallen still aus der Map, damit ein einzelner Defekt den Auftrag nicht
    verhindert.
    """
    try:
        quelle = pfad.read_text(encoding="utf-8")
        baum = ast.parse(quelle)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError, RecursionError, MemoryError):
        return None

    symbole: list[str] = []
    module: list[str] = []
    for knoten in baum.body:
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbole.append(_signatur(knoten))
        elif isinstance(knoten, ast.ClassDef):
            symbole.append(f"class {knoten.name}")
            for kind in knoten.body:
                if isinstance(kind, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbole.append(f"    {_signatur(kind)}")
        elif isinstance(knoten, (ast.Import, ast.ImportFrom)):
            zeile = ast.get_source_segment(quelle, knoten)
            if zeile:
                symbole.append(" ".join(zeile.split()))
            if isinstance(knoten, ast.Import):
                module.extend(alias.name for alias in knoten.names)
            elif knoten.level == 0 and knoten.module:
                module.append(knoten.module)
    return symbole, module


def _modul_pfad(repo_root: Path, modul: str) -> Path | None:
    """Datei zu einem Importnamen (`pkg.mod` -> `pkg/mod.py` oder `pkg/mod/__init__.py`)."""
    teile = modul.split(".")
    if not teile or not all(teile):
        return None
    basis = repo_root.joinpath(*teile)
    for kandidat in (basis.with_suffix(".py"), basis / "__init__.py"):
        if _ist_datei(kandidat) and _unter_root(repo_root, kandidat):
            return kandidat
    return None


def _kapitel(rel: str, symbole: list[str], importiert: bool = False) -> str:
    """Ein Kapitel: Pfadzeile mit `:` und darunter je Symbol eine Zeile."""
    kopf = f"[importiert] {rel}:" if importiert else f"{rel}:"
    return "\n".join([kopf, *symbole])


def _kuerzen(text: str, max_zeichen: int) -> str:
    """An Zeilengrenzen kürzen; reicht eine einzelne Zeile nicht, hart abschneiden."""
    if len(text) <= max_zeichen:
        return text
    zeilen: list[str] = []
    laenge = 0
    for zeile in text.splitlines():
        zusatz = len(zeile) + (1 if zeilen else 0)
        if laenge + zusatz > max_zeichen:
            break
        zeilen.append(zeile)
        laenge += zusatz
    return "\n".join(zeilen)[:max_zeichen]


def build_repomap(repo_root: Path, affected_files: list[str], max_tokens: int = 2000) -> str:
    """Kompakte Symbol-Map der betroffenen Dateien (+ 1-Hop-Importe), "" ohne Treffer.

    - ``affected_files``: relative Pfade ab ``repo_root``; Reihenfolge ist egal,
      das Ergebnis ist deterministisch (sortiert, Duplikate gefiltert).
    - Nur ``.py``-Dateien liefern Symbole (FunctionDef/AsyncFunctionDef/ClassDef
      samt Klassenmethoden, zusätzlich die wörtlichen Importzeilen); fehlende
      Dateien und andere Endungen fliegen still raus, es gibt keine Exception.
    - Kapitel einer betroffenen Datei: erste Zeile ist der relative Pfad mit
      ``:``, darunter je Symbol eine Zeile (``def name(args)`` beziehungsweise
      ``class Name``). Kapitel eines 1-Hop-Importes bekommt ``[importiert] ``
      davor.
    - ``max_tokens`` begrenzt die Länge: mehr als ``max_tokens * 4`` Zeichen
      werden an Zeilengrenzen weggekürzt, falls nötig hart.
    """
    wurzel = Path(repo_root)
    max_zeichen = max(0, max_tokens) * ZEICHEN_PRO_TOKEN

    # Erst alle betroffenen Dateien (sortiert, Duplikate raus), dann die Importe: So bleibt
    # eine Datei, die zugleich betroffen und importiert ist, in ihrem eigenen Kapitel.
    gelesen: list[tuple[str, list[str], list[str]]] = []
    gesehen: set[str] = set()
    for roh in sorted({str(p) for p in affected_files}):
        pfad = wurzel / roh
        if pfad.suffix != ".py" or not _ist_datei(pfad) or not _unter_root(wurzel, pfad):
            continue
        rel = _relativ(wurzel, pfad)
        if rel is None or rel in gesehen:
            continue
        ergebnis = _lies(pfad)
        if ergebnis is None:
            continue
        gesehen.add(rel)
        gelesen.append((rel, ergebnis[0], ergebnis[1]))

    kapitel = [_kapitel(rel, symbole) for rel, symbole, _ in gelesen]

    # Genau eine Ebene: Importe der importierten Dateien werden nicht weiter verfolgt.
    for _, _, module in gelesen:
        for modul in module:
            ziel = _modul_pfad(wurzel, modul)
            if ziel is None or ziel.suffix != ".py":
                continue
            rel = _relativ(wurzel, ziel)
            if rel is None or rel in gesehen:
                continue
            ergebnis = _lies(ziel)
            if ergebnis is None:
                continue
            gesehen.add(rel)
            kapitel.append(_kapitel(rel, ergebnis[0], importiert=True))

    if not kapitel:
        return ""
    return _kuerzen("\n".join(kapitel), max_zeichen)
