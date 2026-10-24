"""Planer-Preflight: prüft einen Issue-Text und seine Akzeptanztests vor dem Einstellen.

Warum es das gibt: Planer ohne Shell legen Worker-Issues mit `entwurf` an, weil sie die
Akzeptanztests nicht ausführen können. Ein kaputter Test fällt dann erst auf, wenn ein Worker
daran scheitert und das Issue eskaliert. Dieses Werkzeug macht die Prüfung zu einem Befehl:
`python scripts/issuecheck.py <issue.md>` prüft den Text formal und lässt den Akzeptanztest
gegen das Repository laufen. Ein roter Test ohne Umsetzung ist das erwünschte Ergebnis.

Nur Standardbibliothek; pytest läuft als Unterprozess.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REQUIRED_HEADINGS = (
    "Ziel",
    "Betroffene Dateien",
    "Schnittstellen",
    "Nicht Teil dieser Aufgabe",
    "Akzeptanztests",
    "Fertig, wenn",
    "Merge-Modus",
    "Kontext",
)

MANUAL_PATHS = (
    "AGENTS.md",
    ".github",
    "skills",
    "docs/main-schutz.ruleset.json",
)

# Importe, die ein Akzeptanztest nicht braucht und die Netzwerk, Uhrzeit oder Zufall ins Spiel bringen.
FORBIDDEN_IMPORTS = frozenset({"requests", "httpx", "socket", "http", "ftplib", "smtplib", "random"})
ALLOWED_IMPORT_ROOTS = frozenset({"pytest", "sandbox", "tests"})

# Aufrufe, die den Testlauf von der Uhr oder vom Zufall abhängig machen.
_FORBIDDEN_CALLS = ("time.time", "time.sleep")
_FORBIDDEN_CALL_ATTRS = frozenset({"now", "utcnow", "today"})
_SKIP_ATTRS = frozenset({"skip", "skipif", "xfail", "importorskip"})
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_WAIT_ON_RE = re.compile(r"Wartet\s+auf\s*:\s*#?\d+", re.IGNORECASE)



@dataclass(frozen=True)
class Finding:
    level: str  # "fehler" | "warnung"
    code: str   # maschinenlesbar
    text: str   # Klartext auf Deutsch


# ---------------- Parsing ----------------
def _headings_outside_fences(lines: list[str]) -> list[tuple[int, int, str]]:
    """(Zeilenindex, Ebene, Text) aller ATX-Überschriften außerhalb von Codeblöcken."""
    in_fence = False
    out: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def _section_lines(issue_md: str, name: str) -> list[str] | None:
    """Zeilen des Abschnitts `name` (ohne Überschrift) bis zur nächsten Überschrift gleicher oder
    höherer Ebene. None, wenn die Überschrift fehlt."""
    lines = issue_md.replace("\r\n", "\n").split("\n")
    heads = _headings_outside_fences(lines)
    for pos, (line_no, level, text) in enumerate(heads):
        if text.lower() != name.lower():
            continue
        end = len(lines)
        for later_no, later_level, _ in heads[pos + 1:]:
            if later_level <= level:
                end = later_no
                break
        return lines[line_no + 1:end]
    return None


def _iter_blocks(lines: list[str]) -> list[list[str]]:
    """Alle Codeblöcke in `lines` als Inhaltszeilen. Zäune ``` oder ~~~, Länge >= 3."""
    blocks: list[list[str]] = []
    i, n = 0, len(lines)
    while i < n:
        m = _FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fence = m.group(1)
        close_re = re.compile(r"^" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$")
        j = i + 1
        body: list[str] = []
        while j < n and not close_re.match(lines[j]):
            body.append(lines[j])
            j += 1
        blocks.append(body)
        break_after = j >= n
        i = j + 1
        if break_after:  # ungeschlossener Zaun: Rest als ein Block werten
            break
    return blocks


def _normalize(code: str) -> str:
    """Zeilenenden normalisieren, Leerraum am Zeilen-/Dateiende entfernen."""
    lines = [ln.rstrip() for ln in code.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def extract_acceptance_block(issue_md: str) -> str | None:
    """Erster Codeblock unter der Überschrift „Akzeptanztests“,
    Zeilenenden normalisiert, Rand-Leerzeilen entfernt. None, wenn es keinen gibt oder er leer ist."""
    section = _section_lines(issue_md, "Akzeptanztests")
    if section is None:
        return None
    blocks = [b for b in _iter_blocks(section) if any(ln.strip() for ln in b)]
    if not blocks:
        return None
    return _normalize("\n".join(blocks[0])) or None


# ---------------- Statische Prüfung ----------------
def _import_roots(tree: ast.AST) -> list[str]:
    """Wurzelmodule aller absoluten Importe, in Reihenfolge, ohne Dubletten."""
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:  # relative Importe ignorieren
                continue
            roots.append(node.module.split(".")[0])
    seen: list[str] = []
    for root in roots:
        if root not in seen:
            seen.append(root)
    return seen


def _string_literals(tree: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _dotted(node: ast.AST) -> str | None:
    """Gepunkteter Name eines Attribut-/Namens-Zugriffs („time.sleep“), sonst None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _skip_marker(tree: ast.AST) -> str | None:
    """Erster Attribut-Zugriff auf skip/skipif/xfail/importorskip im Code, sonst None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _SKIP_ATTRS:
            return _dotted(node) or node.attr
    return None


def _forbidden_call(tree: ast.AST) -> str | None:
    """Erster Uhrzeit-/Zufall-Zugriff im Code, sonst None. `time.time`, `time.sleep`,
    jeder `*.now`/`*.utcnow`/`*.today` und alles unter `random.`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        name = _dotted(node)
        if not name:
            continue
        if name in _FORBIDDEN_CALLS or name.split(".")[-1] in _FORBIDDEN_CALL_ATTRS:
            return name
        if name.split(".")[0] == "random":
            return name
    return None


def check_static(issue_md: str) -> list[Finding]:
    """Prüft den Issue-Text ohne Ausführung (formale Vorgaben und Akzeptanztest-Regeln)."""
    findings: list[Finding] = []
    text = issue_md.replace("\r\n", "\n")
    lines = text.split("\n")

    # 1. Überschriften prüfen
    found_headings = [name for _, _, name in _headings_outside_fences(lines)]
    headings_set = {name.lower() for name in found_headings}
    for name in REQUIRED_HEADINGS:
        if name.lower() not in headings_set:
            findings.append(Finding("fehler", "ueberschrift-fehlt", f"Pflichtüberschrift fehlt: ### {name}"))

    # Reihenfolge prüfen
    idx_map = {name.lower(): i for i, (_, _, name) in enumerate(_headings_outside_fences(lines))}
    last_idx = -1
    for name in REQUIRED_HEADINGS:
        if name.lower() in idx_map:
            curr_idx = idx_map[name.lower()]
            if curr_idx < last_idx:
                findings.append(Finding("warnung", "reihenfolge",
                                        f"Überschrift ### {name} steht nicht in der vorgeschriebenen Reihenfolge."))
            last_idx = curr_idx

    # 2. Betroffene Dateien
    dateien_lines = _section_lines(text, "Betroffene Dateien")
    if dateien_lines is not None:
        dateien_text = "\n".join(dateien_lines)
        if "tests/acceptance/test_issue_" not in dateien_text:
            findings.append(Finding("fehler", "betroffene-dateien-test",
                                    "Abschnitt „Betroffene Dateien“ nennt nicht `tests/acceptance/test_issue_<N>.py`."))

    # 3. Merge-Modus
    merge_lines = _section_lines(text, "Merge-Modus")
    merge_is_auto = False
    if merge_lines is not None:
        merge_low = "\n".join(merge_lines).lower()
        if "automatisch" in merge_low:
            merge_is_auto = True
        elif "manuell" in merge_low:
            merge_is_auto = False
        else:
            findings.append(Finding("fehler", "merge-modus",
                                    "Abschnitt „Merge-Modus“ nennt weder „automatisch nach Review“ noch „manuell durch Stephan“."))

        # Prüfen, ob manual_paths berührt werden
        if merge_is_auto and dateien_lines is not None:
            touched_manual = [p for p in MANUAL_PATHS if p in dateien_text]
            for m in re.finditer(r"tests/acceptance/test_issue_(\d+)\.py", dateien_text):
                touched_manual.append(m.group(0))
            if touched_manual:
                findings.append(Finding("fehler", "manual-paths-automatisch",
                                        f"Aufsichtsdatei berührt ({', '.join(touched_manual)}): "
                                        "Merge-Modus darf nicht automatisch sein (Regel 4: manuell durch Stephan erforderlich)."))

    # 4. Akzeptanztests prüfen
    section = _section_lines(text, "Akzeptanztests")
    if section is None:  # fehlende Überschrift ist bereits gemeldet
        return _dedupe(findings)

    # Versehentliche Abhängigkeitszeilen in Akzeptanztests abfangen (#46)
    for line in section:
        if _WAIT_ON_RE.search(line):
            findings.append(Finding("fehler", "wartet-auf-im-test",
                                    f"Zeile '{line.strip()}' im Akzeptanztest-Bereich blockiert den Review-Takt dauerhaft."))

    blocks = [b for b in _iter_blocks(section) if any(ln.strip() for ln in b)]
    if not blocks:
        findings.append(Finding("fehler", "kein-block",
                                "Unter „Akzeptanztests“ steht kein Codeblock (Feld leer oder nicht ausgefüllt)."))
        return _dedupe(findings)
    if len(blocks) > 1:
        findings.append(Finding("fehler", "mehrere-bloecke",
                                f"Unter „Akzeptanztests“ stehen {len(blocks)} Codeblöcke; erwartet wird genau einer."))

    code = _normalize("\n".join(blocks[0]))
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        findings.append(Finding("fehler", "syntaxfehler",
                                f"Der Akzeptanztest ist kein gültiges Python: {exc.msg} (Zeile {exc.lineno})."))
        return _dedupe(findings)

    if not any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in ast.walk(tree)):
        findings.append(Finding("fehler", "kein-test", "Der Codeblock enthält keine Funktion test_*."))

    for root in _import_roots(tree):
        if root in FORBIDDEN_IMPORTS:
            findings.append(Finding("fehler", "verbotener-import", f"Verbotener Import: {root}"))
        elif root not in ALLOWED_IMPORT_ROOTS and root not in sys.stdlib_module_names:
            findings.append(Finding("warnung", "fremder-import",
                                    f"Import außerhalb von Standardbibliothek/pytest/sandbox/tests: {root}"))

    marker = _skip_marker(tree)
    if marker:
        findings.append(Finding("fehler", "skip-xfail", f"Der Akzeptanztest überspringt Tests: {marker}"))

    call = _forbidden_call(tree)
    if call:
        findings.append(Finding("fehler", "verbotener-aufruf",
                                f"Uhrzeit, Zufall oder Warten im Akzeptanztest: {call}"))

    # Verbotene Pfade wie /root (Fehler aus #39)
    for value in _string_literals(tree):
        if value.startswith("/root") or value.startswith("/root/"):
            findings.append(Finding("fehler", "root-pfad",
                                    f"Akzeptanztest verwendet absoluten Pfad '{value}'; Vorgabe ist tmp_path."))

    return _dedupe(findings)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str]] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.code, f.text)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


# ---------------- Testlauf ----------------
def _count(output: str, word: str) -> int:
    m = re.search(r"(\d+) " + word, output)
    return int(m.group(1)) if m else 0


def check_red(issue_md: str, repo: Path, timeout: float = 300, keep_tmp: bool = False, verbose: bool = False) -> tuple[list[Finding], str]:
    """Führt den Akzeptanztest mit pytest gegen `repo` aus und bewertet das Ergebnis.
    Rückgabe: (Befunde, pytest_ausgabe)"""
    block = extract_acceptance_block(issue_md)
    if block is None:
        return [], ""
    repo = Path(repo).resolve()
    env = dict(os.environ)
    # Repo-Wurzel und sandbox/ in PYTHONPATH aufnehmen
    sandbox_dir = repo / "sandbox"
    pythonpaths = [str(repo)]
    if sandbox_dir.is_dir():
        pythonpaths.append(str(sandbox_dir))
    if env.get("PYTHONPATH"):
        pythonpaths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpaths)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    # Viele Akzeptanztests finden das Repo über Path(__file__).parents[2]; aus einem
    # Temp-Ordner zeigt das ins Leere (#175). Liegt der Akzeptanzordner vor, deshalb dort
    # ausführen — es wird nie ein Ordner angelegt.
    acceptance_dir = repo / "tests" / "acceptance"
    tmp_dir: Path | None = None
    if acceptance_dir.is_dir():
        testfile = acceptance_dir / f"test_issue_preflight_{os.getpid()}.py"
    else:
        tmp_dir = Path(tempfile.mkdtemp(prefix="issuecheck-"))
        testfile = tmp_dir / "test_issue_preflight.py"
    try:
        testfile.write_text(block + "\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", str(testfile)]
        try:
            proc = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ([Finding("fehler", "timeout",
                             f"Der Akzeptanztest lief länger als {timeout:g}s; vermutlich wartet er auf Zeit oder Netz.")], "")
        except OSError as exc:
            return ([Finding("fehler", "pytest-start", f"pytest ließ sich nicht starten: {exc}")], "")

        output = f"{proc.stdout}\n{proc.stderr}"
    finally:
        if not keep_tmp:
            try:
                testfile.unlink(missing_ok=True)
                if tmp_dir is not None:
                    tmp_dir.rmdir()
            except OSError:
                pass
        else:
            print(f"[issuecheck] Temporäre Testdatei behalten: {testfile}", file=sys.stderr)

    findings: list[Finding] = []
    if proc.returncode == 0:
        findings.append(Finding("fehler", "schon-gruen",
                                "Alle Akzeptanztests sind ohne Umsetzung grün; sie prüfen die neue Funktion nicht."))
    elif proc.returncode == 5:
        findings.append(Finding("fehler", "kein-test", "pytest hat im Akzeptanztest keine Testfunktion gesammelt."))
    elif proc.returncode == 4:
        findings.append(Finding("fehler", "pytest-usage", f"pytest Kommandozeilenfehler:\n{output.strip()}"))
    elif "ERROR collecting" in output or "SyntaxError:" in output:
        # Wenn der Sammlungsfehler ein fehlender Import aus sandbox/ ist, ist das ein erwarteter Rot-Grund
        if "ModuleNotFoundError" not in output and "ImportError" not in output:
            findings.append(Finding("fehler", "sammlungsfehler",
                                    f"pytest konnte den Test nicht sammeln (Syntax-/Strukturfehler):\n{output.strip()}"))

    if _count(output, "passed") > 0:
        findings.append(Finding("warnung", "teilweise-gruen",
                                "Ein Teil der Akzeptanztests ist ohne Umsetzung schon grün."))

    return findings, output


# Der pre-push-Hook führt TEST_CMD als root aus, dort umgeht CAP_DAC_OVERRIDE alle Modusbits.
_ROOT_PLUGIN = (
    "# vom Preflight erzeugt: simuliert den pre-push-Hook, dort läuft TEST_CMD als root.\n"
    "import os\n"
    "os.getuid = lambda: 0\n"
    "os.getgid = lambda: 0\n"
)

# Befunde, die den Lauf selbst betreffen: ein zweiter Durchgang brächte nichts und würde nur
# einen weiteren kaputten Lauf starten. „schon-gruen“ und „teilweise-gruen“ sind dagegen
# Ergebnisse des Blocks und dürfen die Rollenprüfung nicht verhindern.
_LAUF_GESCHEITERT = frozenset({"timeout", "pytest-start", "pytest-usage", "sammlungsfehler", "kein-test"})


def _nutzer_plugin(uid: int) -> str:
    """Plugin für die Gegenrichtung: ist der normale Lauf schon der root-Lauf (Preflight
    selbst als root, etwa im pre-push-Hook), simuliert die zweite Runde einen unprivilegierten
    Nutzer. Ohne sie wäre die Laufnutzer-Abhängigkeit genau dort unsichtbar, wo sie den Push
    blockiert (#327)."""
    return (
        "# vom Preflight erzeugt: simuliert einen Lauf als unprivilegierter Nutzer.\n"
        "import os\n"
        f"os.getuid = lambda: {uid}\n"
        f"os.getgid = lambda: {uid}\n"
    )


def _failed_ids(pytest_ausgabe: str) -> set[str]:
    """Node-IDs aller Zeilen, die mit „FAILED “ beginnen (`FAILED tests/x.py::test_y - …`
    → `tests/x.py::test_y`). Leeres Set, wenn keine solche Zeile vorkommt."""
    ids: set[str] = set()
    for line in pytest_ausgabe.splitlines():
        if not line.startswith("FAILED "):
            continue
        node = line[len("FAILED "):].split(" - ", 1)[0].strip()
        if node:
            ids.add(node)
    return ids


def _test_namen(node_ids: set[str]) -> set[str]:
    """Vergleichsschlüssel ohne Pfad: beide Läufe schreiben den Block in ein eigenes
    Temp-Verzeichnis, die Node-ID unterscheidet sich daher im Pfad, nicht im Testnamen."""
    return {node.split("::", 1)[-1] for node in node_ids}


def check_rollen(issue_md: str, repo: Path, normal_ausgabe: str = "", timeout: float = 300,
                 verbose: bool = False) -> tuple[list[Finding], str]:
    """Führt den Akzeptanzblock zusätzlich als root aus und meldet laufnutzerabhängige Tests.

    Ablauf: Block über `extract_acceptance_block()` holen (sonst `([], "")`). Fehlt `getuid`
    (Windows), ist keine Rollentrennung möglich → `([], "")`. Sonst den Block in ein
    Verzeichnis unter `tempfile.mkdtemp(prefix="issuecheck-root-")` schreiben (Plugin
    daneben), mit eigenem `--basetemp` und `PYTHONPATH` = Plugin-Verzeichnis + Repo +
    `sandbox/` ausführen (`cwd=repo`) und beide Ausgaben über `_failed_ids()` vergleichen.
    Ein Test, der nur in der root-Rolle rot ist, ergibt den Befund `laufnutzerabhaengig`.
    Temp-Verzeichnisse werden im `finally` entfernt; ein nicht startbarer zweiter Lauf
    ergibt nur eine Warnung, damit ein kaputter Preflight den Planer nicht blockiert.
    """
    block = extract_acceptance_block(issue_md)
    if block is None:
        return [], ""
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return [], ""
    # Ist der Preflight selbst root und liegt die Ausgabe des normalen Laufs vor, war dieser
    # der Hook-Lauf; dann prüft die zweite Runde die unprivilegierte Rolle. Ohne Ausgabe
    # (direkter Aufruf) übernimmt sie die root-Rolle — sonst bliebe die Abhängigkeit genau
    # dort unsichtbar, wo sie den Push blockiert (#327).
    normale_rolle_root = getuid() == 0 and bool(normal_ausgabe.strip())

    repo = Path(repo).resolve()
    plugin_dir = Path(tempfile.mkdtemp(prefix="issuecheck-root-"))
    basetemp: Path | None = None
    try:
        basetemp = Path(tempfile.mkdtemp(prefix="issuecheck-root-basetemp-"))
        plugin = "issuecheck_nutzer_plugin" if normale_rolle_root else "issuecheck_root_plugin"
        plugin_text = _nutzer_plugin(1000) if normale_rolle_root else _ROOT_PLUGIN
        (plugin_dir / f"{plugin}.py").write_text(plugin_text, encoding="utf-8")
        testfile = plugin_dir / "test_issue_preflight.py"
        testfile.write_text(block + "\n", encoding="utf-8")

        env = dict(os.environ)
        pythonpaths = [str(plugin_dir), str(repo)]
        sandbox_dir = repo / "sandbox"
        if sandbox_dir.is_dir():
            pythonpaths.append(str(sandbox_dir))
        if env.get("PYTHONPATH"):
            pythonpaths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pythonpaths)
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
               "-p", plugin, "--basetemp", str(basetemp), str(testfile)]
        try:
            proc = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return ([Finding("warnung", "root-lauf-nicht-ausfuehrbar",
                             f"Der zweite Lauf lief länger als {timeout:g}s und wurde abgebrochen; "
                             "Laufnutzer-Abhängigkeit bleibt ungeprüft.")], "")
        except OSError as exc:
            return ([Finding("warnung", "root-lauf-nicht-ausfuehrbar",
                             f"Der zweite Lauf ließ sich nicht starten: {exc}; "
                             "Laufnutzer-Abhängigkeit bleibt ungeprüft.")], "")
        zweite_ausgabe = f"{proc.stdout}\n{proc.stderr}"
    finally:
        shutil.rmtree(plugin_dir, ignore_errors=True)
        if basetemp is not None:
            shutil.rmtree(basetemp, ignore_errors=True)

    if proc.returncode == 4:
        return ([Finding("warnung", "root-lauf-nicht-ausfuehrbar",
                         f"Der zweite Lauf endete mit einem pytest-Aufruffehler:\n{zweite_ausgabe.strip()}")],
                zweite_ausgabe)

    if normale_rolle_root:
        root_ids = _test_namen(_failed_ids(normal_ausgabe))
        andere_ids = _test_namen(_failed_ids(zweite_ausgabe))
    else:
        root_ids = _test_namen(_failed_ids(zweite_ausgabe))
        andere_ids = _test_namen(_failed_ids(normal_ausgabe))
    mehr = root_ids - andere_ids
    if not mehr:
        return [], zweite_ausgabe

    return (
        [Finding("fehler", "laufnutzerabhaengig",
                 "Akzeptanztest hängt vom Laufnutzer ab: als root (pre-push-Hook) zusätzlich rot: "
                 f"{', '.join(sorted(mehr)[:3])}. Zielnutzer fremd faken (uid != 0, Dateieigentümer ungleich) "
                 "und über die Sonstige-Bits entscheiden; Elternordner nur mit x für Fremde "
                 "(`| 0o011`) freigeben.")],
        zweite_ausgabe,
    )


def validate(issue_md: str, repo: Path, run_tests: bool = True, keep_tmp: bool = False, verbose: bool = False) -> tuple[list[Finding], str]:
    """check_static; bei Fehlern oder run_tests=False nur das. Sonst zusätzlich check_red und
    check_rollen (zweiter Lauf in der root-Rolle). Die Ausgaben beider Läufe werden verkettet."""
    findings = check_static(issue_md)
    output = ""
    if not run_tests or any(f.level == "fehler" for f in findings):
        return findings, output
    red_findings, output = check_red(issue_md, Path(repo), keep_tmp=keep_tmp, verbose=verbose)
    findings += red_findings
    if any(f.code in _LAUF_GESCHEITERT for f in red_findings):
        return findings, output
    rollen_findings, rollen_ausgabe = check_rollen(issue_md, Path(repo), normal_ausgabe=output,
                                                   verbose=verbose)
    findings += rollen_findings
    if rollen_ausgabe:
        output = f"{output}\n{rollen_ausgabe}"
    return findings, output


# ---------------- Bericht und CLI ----------------
def format_report(findings: list[Finding], pytest_output: str = "", verbose: bool = False) -> str:
    """Eine Zeile je Befund, zuletzt das Urteil für den Planer."""
    lines: list[str] = []
    errors = 0
    warnings = 0
    for f in findings:
        if f.level == "fehler":
            lines.append(f"❌ [{f.code}] {f.text}")
            errors += 1
        else:
            lines.append(f"⚠️  [{f.code}] {f.text}")
            warnings += 1

    if verbose and pytest_output:
        lines.append("\n--- pytest Ausgabe ---")
        lines.append(pytest_output.strip())
        lines.append("----------------------\n")

    if errors:
        lines.append(f"\nNicht einstellen: {errors} Fehler, {warnings} Warnungen.")
    elif warnings:
        lines.append(f"\nAkzeptanztests sind valide (mit {warnings} Warnungen) und bereit für die Sandbox.")
    else:
        lines.append("\nAkzeptanztests sind valide und bereit für die Sandbox.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI Einstiegspunkt."""
    parser = argparse.ArgumentParser(prog="python scripts/issuecheck.py",
                                     description="Issue-Text und Akzeptanztests vor dem Einstellen prüfen")
    parser.add_argument("datei", help="Issue-Datei oder „-“ für stdin")
    parser.add_argument("--repo", default=".", help="Repo-Wurzel für den Testlauf (Standard: .)")
    parser.add_argument("--format-only", "--no-run", dest="no_run", action="store_true",
                        help="Akzeptanztests nicht mit pytest ausführen (nur Syntax, AST und Struktur)")
    parser.add_argument("--keep-tmp", action="store_true", help="Temporäre Testdatei nicht löschen")
    parser.add_argument("--verbose", "-v", action="store_true", help="Ausführliche Ausgabe inklusive pytest Output")
    args = parser.parse_args(argv)

    if args.datei == "-":
        issue_md = sys.stdin.read()
    else:
        datei_path = Path(args.datei)
        if not datei_path.is_file():
            print(f"Fehler: Datei '{args.datei}' existiert nicht.", file=sys.stderr)
            return 1
        issue_md = datei_path.read_text(encoding="utf-8")

    findings, pytest_out = validate(issue_md, Path(args.repo), run_tests=not args.no_run,
                                    keep_tmp=args.keep_tmp, verbose=args.verbose)
    print(format_report(findings, pytest_output=pytest_out, verbose=args.verbose))
    return 1 if any(f.level == "fehler" for f in findings) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
