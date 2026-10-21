"""Deterministischer Rahmen um den Worker-Agenten: Branch, Akzeptanztest, PR-Text.

Der Sprachmodell-Agent scheitert regelmäßig an den rein mechanischen Pflichtschritten eines
Worker-Auftrags (Ziel-Branch anlegen, Akzeptanztest-Block zeichengenau sichern, die PR-Vorlage
ausfüllen). Diese Funktionen erledigen das ohne Modell und ohne Netzwerk. Teil 1 stellt nur das
Modul bereit; das Einhängen in den Takt folgt in einem eigenen Issue.
"""

import re
import subprocess
from pathlib import Path
from typing import Callable, Sequence

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]

_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")
_FENCE_LINE_RE = re.compile(r"^(`{3,}|~{3,})")
# Wie `acceptance_guard.py`: nur ein Block, dessen Zaun mit demselben Zeichen und derselben Länge
# schließt, ist ein Codeblock (```python … ``` eingeschlossen).
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n(.*?)\n?^\1[ \t]*$", re.M | re.S)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BARE_CLOSES_RE = re.compile(r"\bCloses\s*#(?!\d)", re.I)

# Reihenfolge der Abschnitte, die ein vertragskonformer PR-Text mindestens nennt.
_MIN_HEADINGS = (
    "## Was und warum",
    "## Wie getestet",
    "## Offene Punkte",
    "## Umgesetzt von / Review durch",
)


def _normalize_code(code: str) -> str:
    """Zeilenenden vereinheitlichen, Leerraum am Zeilen- und Blockende entfernen."""
    lines = [line.rstrip() for line in code.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def extract_acceptance_block(issue_md: str | None) -> str | None:
    """Erster nicht-leerer Codeblock unter der Überschrift „Akzeptanztests“.

    Jede ATX-Ebene (#..######) zählt, Groß-/Kleinschreibung ist egal; die Sektion endet an der
    nächsten Überschrift gleicher oder höherer Ebene (Überschriften in Codeblöcken zählen nicht).
    ``None``, wenn der Text leer ist, die Sektion fehlt, keinen Codeblock enthält oder alle Blöcke
    nur aus Leerzeilen bestehen. Reine Funktion ohne I/O.
    """
    if not issue_md:
        return None
    lines = issue_md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = level = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and m.group(2).lower().startswith("akzeptanztests"):
            start, level = i + 1, len(m.group(1))
            break
    if start is None:
        return None

    end = len(lines)
    in_fence = False
    for i in range(start, len(lines)):
        if _FENCE_LINE_RE.match(lines[i]):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(lines[i])
        if m and len(m.group(1)) <= level:
            end = i
            break

    section = "\n".join(lines[start:end])
    for fence in _FENCE_RE.finditer(section):
        code = _normalize_code(fence.group(2))
        if code:
            return code
    return None


def _run_checked(cmd: list[str], workdir: Path, run: Runner) -> None:
    result = run(cmd)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        message = f"git-Befehl fehlgeschlagen ({result.returncode}) in {workdir}: {' '.join(cmd)}"
        raise RuntimeError(f"{message}: {detail}" if detail else message)


def setup_issue_branch(
    workdir: str | Path,
    issue_no: int,
    issue_body: str | None,
    run: Runner,
) -> Path | None:
    """Legt den Branch ``feature/issue-<issue_no>`` an und sichert den Akzeptanztest als ersten Commit.

    Der Branch wird per ``checkout -B`` gesetzt, damit ein Rest aus einem früheren Lauf nicht
    blockiert. Enthält das Issue einen Akzeptanztest-Block, entsteht
    ``<workdir>/tests/acceptance/test_issue_<issue_no>.py`` mit genau einem abschließenden
    Zeilenumbruch und wird über ``run`` gestaged und committet. Rückgabe ist der Pfad der Datei
    oder ``None`` ohne Block. Ein ``returncode != 0`` eines ``run``-Aufrufs wirft ``RuntimeError``.
    """
    base = Path(workdir)
    _run_checked(["git", "checkout", "-B", f"feature/issue-{issue_no}"], base, run)

    block = extract_acceptance_block(issue_body)
    if block is None:
        return None

    target = base / "tests" / "acceptance" / f"test_issue_{issue_no}.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(block + "\n", encoding="utf-8")

    relative = f"tests/acceptance/test_issue_{issue_no}.py"
    _run_checked(["git", "add", relative], base, run)
    _run_checked(["git", "commit", "-m", f"Akzeptanztests aus #{issue_no}"], base, run)
    return target


def _heading_texts(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if _HEADING_RE.match(line)]


def _has_heading(body: str, heading: str) -> bool:
    """Überschrift vorhanden? Der Wortlaut darf weitergehen („## Offene Punkte / Abweichungen“)."""
    wanted = heading.lstrip("#").strip().lower()
    return any(text.lstrip("#").strip().lower().startswith(wanted) for text in _heading_texts(body))


def _section_end(body: str, start: int) -> int:
    """Position der nächsten Überschrift nach ``start`` (oder Textende)."""
    m = re.search(r"^#{1,6}\s", body[start:], re.M)
    return start + m.start() if m else len(body)


def _insert_in_section(body: str, heading_re: str, content: str, only_if_empty: bool = True) -> str:
    """Setzt ``content`` direkt unter die passende Überschrift."""
    m = re.search(heading_re, body, re.M)
    if not m:
        return body
    end = _section_end(body, m.end())
    if only_if_empty and body[m.end():end].strip():
        return body
    return body[:m.end()] + f"\n\n{content}\n\n" + body[end:]


def build_pr_body(
    template: str,
    issue_no: int,
    worker: str,
    summary: str = "",
) -> str:
    """Baut aus der PR-Vorlage einen vertragskonformen PR-Text.

    - HTML-Kommentare der Vorlage entfallen; Leerraum wird zusammengefasst.
    - ``Closes #`` ohne Nummer wird zu ``Closes #<issue_no>``; fehlt der Verweis ganz, steht er
      unter ``## Was und warum`` (fehlt auch die Überschrift, wird sie ergänzt).
    - Eine nicht-leere ``summary`` steht (getrimmt) unter dem ``Closes``-Verweis.
    - Alle Überschriften der Vorlage bleiben erhalten; fehlen die Pflichtabschnitte, werden sie
      mit ihrem Mindestinhalt angehängt.
    - Unter ``## Offene Punkte`` steht ``keine``, unter ``## Umgesetzt von / Review durch`` die
      Zeile ``Worker: <worker>``, sofern der Abschnitt nicht schon Inhalt hat.

    Reine Funktion ohne Netzwerk und ohne Dateisystemzugriff.
    """
    body = (template or "").replace("\r\n", "\n").replace("\r", "\n")
    body = _HTML_COMMENT_RE.sub("", body)
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    summary = summary.strip()
    closes_line = f"Closes #{issue_no}"
    body = _BARE_CLOSES_RE.sub(closes_line, body)

    if not _has_heading(body, "## Was und warum"):
        body = (body + f"\n\n## Was und warum\n\n{closes_line}").strip()
    elif not re.search(rf"\bCloses #{issue_no}\b", body, re.I):
        body = _insert_in_section(
            body, r"^##\s*Was und warum\s*$", closes_line, only_if_empty=False
        )

    if summary:
        # `[ \t]*` statt `\s*`: sonst schluckt das Muster die Leerzeile vor dem nächsten Abschnitt.
        closes_re = re.compile(rf"^{re.escape(closes_line)}[ \t]*$", re.M)
        m = closes_re.search(body)
        if m:
            body = body[:m.end()] + f"\n\n{summary}" + body[m.end():]
        else:
            # Closes-Verweis steht mitten in einer Zeile: dann nur die Zusammenfassung ergänzen,
            # statt den Verweis ein zweites Mal zu setzen.
            body = _insert_in_section(
                body, r"^##\s*Was und warum\s*$", summary, only_if_empty=False
            )

    for heading in _MIN_HEADINGS:
        if not _has_heading(body, heading):
            body = (body + f"\n\n{heading}").strip()

    body = _insert_in_section(body, r"^##\s*Offene Punkte.*$", "keine")
    body = _insert_in_section(body, r"^##\s*Umgesetzt von\s*/\s*Review durch\s*$", f"Worker: {worker}")

    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body + "\n"
