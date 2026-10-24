"""Code-Aufgaben des Benchmarks und die abgesicherte Prüfung von Modellcode (#335).

Teil 3 von 5 des Benchmark-Moduls. Das Modul ruft kein Modell auf und schreibt keine Ergebnisse:
Es hält drei kleine Aufgaben (`CODE_TASKS`), baut daraus den Prompt, zieht den Python-Block aus
einer Antwort und führt den Code gegen ein Prüfskript aus (`run_pruefung`, `score_code`).

Der Code stammt von fremden Modellen und läuft auf der Box, auf der die Worker als root arbeiten.
`run_pruefung` erzwingt deshalb:

- ein eigenes temporäres Verzeichnis als Arbeitsverzeichnis (Unterverzeichnis `arbeit`, Ausgaben in
  `aus`), das nach dem Lauf restlos verschwindet — auch bei Zeitlimit und Fehlern
- eine Umgebung, die nur `PATH=/usr/bin:/bin` enthält, leeres `stdin`, `stdout`/`stderr` in
  Dateien; Ausgaben des Modellcodes werden nie in den Grund übernommen
- eine eigene Sitzung; bei Zeitlimit wird die ganze Prozessgruppe mit `SIGKILL` beendet, nach
  jedem Lauf werden zurückgebliebene Kindprozesse erschlagen
- Ressourcengrenzen im Kindprozess: 512 MiB Adressraum, `ceil(timeout_s) + 1` Sekunden CPU-Zeit,
  1 MiB Dateigröße
- eine Netz-Sperre im Prüfskript (`socket.socket`, `socket.create_connection` und
  `socket.getaddrinfo` lösen `RuntimeError("NETZ_VERBOTEN")` aus)
- Bestehen nur bei Exitcode 0 **und** einem Zufallsmarker am Ende von `stdout`, den der Nachspann
  des Prüfskripts druckt — ein `sys.exit(0)` oder `os._exit(0)` im Modellcode täuscht damit kein
  Bestehen vor

Ausdrücklich **nicht** erzwungen (Betriebsentscheidung, nicht Teil dieses Moduls):

- kein Dateisystem-Schutz außerhalb des Arbeitsverzeichnisses: Pfade wie `/etc` bleiben lesbar,
  wenn der Prozess als root läuft
- keine echte Netz-Isolierung: Unterprozesse und `ctypes` umgehen die Socket-Sperre
- kein Schutz gegen absichtliches Täuschen: das Prüfskript liegt im Arbeitsverzeichnis
"""
from __future__ import annotations

import math
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable

try:  # Ressourcengrenzen gibt es nur auf POSIX-Systemen.
    import resource
except ImportError:  # pragma: no cover - Windows ist kein Zielsystem
    resource = None  # type: ignore[assignment]

GRUND_KEIN_CODE = "kein Code"
GRUND_ZEITLIMIT = "Zeitlimit"
GRUND_NETZ = "Netzzugriff verboten"
GRUND_FEHLGESCHLAGEN = "Prüfung fehlgeschlagen"

ADRESSRAUM_BYTES = 512 * 2**20
DATEIGROESSE_BYTES = 1 * 2**20
_NETZ_MARKER = b"NETZ_VERBOTEN"
_NETZ_FENSTER_BYTES = 64 * 1024

_ANTWORTVORGABE = """\
Antworte mit genau einem Codeblock in der Form

```python
# hier der vollständige Code
```

Der Block enthält den vollständigen, lauffähigen Python-Code mit allen nötigen Importen, keine
Beispielaufrufe und kein Hauptprogramm. Eine Erklärung außerhalb des Blocks ist nicht nötig."""

_VORSPANN = '''\
import os as _os
import socket as _socket
import sys as _sys


class _VerbotenesSocket:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("NETZ_VERBOTEN")


def _netz_verboten(*args, **kwargs):
    raise RuntimeError("NETZ_VERBOTEN")


_socket.socket = _VerbotenesSocket
_socket.create_connection = _netz_verboten
_socket.getaddrinfo = _netz_verboten

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
'''

_PRUEFUNG_DAUER = '''\
from loesung import parse_dauer

assert parse_dauer("1h30m") == 5400
assert parse_dauer("2d") == 172800
assert parse_dauer("1d2h3m4s") == 93784
assert parse_dauer("45s") == 45
assert parse_dauer("0s") == 0
assert parse_dauer("10m") == 600
assert parse_dauer("3h") == 10800

falsch = [
    "",
    "abc",
    "5",
    "5x",
    "1h30",
    "30m1h",
    "1h1h",
    "1 h",
    " 1h",
    "1h ",
    "1h\\n",
    "-1h",
    "1H",
    "1d2d",
    "1m2h",
    "h1",
    "1h30s2m",
]
for text in falsch:
    try:
        ergebnis = parse_dauer(text)
    except ValueError:
        continue
    raise AssertionError("ValueError erwartet fuer %r, bekam %r" % (text, ergebnis))
'''

_PRUEFUNG_RATE = '''\
from loesung import RateLimiter


class Uhr:
    def __init__(self):
        self.jetzt = 0.0
        self.aufrufe = 0

    def __call__(self):
        self.aufrufe += 1
        return self.jetzt


uhr = Uhr()
begrenzer = RateLimiter(2, 10.0, uhr)
assert uhr.aufrufe == 0, "der Konstruktor darf die Uhr nicht fragen"

assert begrenzer.erlaubt() is True
assert uhr.aufrufe == 1, "erlaubt() muss die Uhr genau einmal fragen"
uhr.jetzt = 1.0
assert begrenzer.erlaubt() is True
assert uhr.aufrufe == 2
uhr.jetzt = 2.0
assert begrenzer.erlaubt() is False
assert uhr.aufrufe == 3
uhr.jetzt = 3.0
assert begrenzer.erlaubt() is False
assert uhr.aufrufe == 4

# Abgelehnte Aufrufe zaehlen nicht: nach Ablauf des Fensters ist wieder Platz.
uhr.jetzt = 10.5
assert begrenzer.erlaubt() is True
assert uhr.aufrufe == 5
uhr.jetzt = 10.5
assert begrenzer.erlaubt() is False
assert uhr.aufrufe == 6

# Das Fenster ist links offen: t == jetzt - fenster_s liegt ausserhalb.
uhr2 = Uhr()
eng = RateLimiter(1, 5.0, uhr2)
assert eng.erlaubt() is True
uhr2.jetzt = 5.0
assert eng.erlaubt() is True

for max_aufrufe, fenster_s in [(0, 1.0), (-1, 1.0), (1, 0.0), (1, -2.0)]:
    try:
        RateLimiter(max_aufrufe, fenster_s, Uhr())
    except ValueError:
        continue
    raise AssertionError("ValueError erwartet fuer %r/%r" % (max_aufrufe, fenster_s))
'''

_PRUEFUNG_WARTET = '''\
from loesung import parse_wartet_auf

text = (
    "Einleitung ohne Treffer #99\\n"
    "Wartet auf: #12, #3\\n"
    "\\twartet auf: #3 #7\\n"
    "Siehe Wartet auf: #42\\n"
    "WARTET AUF: #5 und 77\\n"
    "Wartet auf: Nummer 77, #8\\n"
    "\\n"
    "```text\\n"
    "Wartet auf: #1\\n"
    "```\\n"
    "Wartet auf: #12\\n"
    "  ```python\\n"
    "Wartet auf: #2\\n"
)
assert parse_wartet_auf(text) == [3, 5, 7, 8, 12]

assert parse_wartet_auf("") == []
assert parse_wartet_auf("Keine Abhaengigkeiten.\\n#3 und #4\\n") == []
assert parse_wartet_auf("Wartet auf:\\n") == []
assert parse_wartet_auf("  Wartet auf: #9, #10\\n") == [9, 10]
'''

_REFERENZ_DAUER = '''\
import re

_MUSTER = re.compile(r"(?:(\\d+)d)?(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?")


def parse_dauer(text: str) -> int:
    if not text:
        raise ValueError(text)
    treffer = _MUSTER.fullmatch(text)
    if treffer is None or not any(treffer.groups()):
        raise ValueError(text)
    tage, stunden, minuten, sekunden = (int(x or 0) for x in treffer.groups())
    return tage * 86400 + stunden * 3600 + minuten * 60 + sekunden
'''

_REFERENZ_RATE = '''\
from typing import Callable


class RateLimiter:
    def __init__(self, max_aufrufe: int, fenster_s: float, uhr: Callable[[], float]):
        if max_aufrufe < 1:
            raise ValueError("max_aufrufe muss mindestens 1 sein")
        if fenster_s <= 0:
            raise ValueError("fenster_s muss positiv sein")
        self._max = max_aufrufe
        self._fenster = fenster_s
        self._uhr = uhr
        self._erlaubt: list[float] = []

    def erlaubt(self) -> bool:
        jetzt = self._uhr()
        self._erlaubt = [zeit for zeit in self._erlaubt if zeit > jetzt - self._fenster]
        if len(self._erlaubt) >= self._max:
            return False
        self._erlaubt.append(jetzt)
        return True
'''

_REFERENZ_WARTET = '''\
import re

_KOPF = re.compile(r"[ \\t]*wartet auf:", re.IGNORECASE)
_NUMMER = re.compile(r"#(\\d+)")


def parse_wartet_auf(text: str) -> list[int]:
    treffer: set[int] = set()
    im_block = False
    for zeile in text.splitlines():
        if zeile.lstrip().startswith("```"):
            im_block = not im_block
            continue
        if im_block or _KOPF.match(zeile) is None:
            continue
        treffer.update(int(nummer) for nummer in _NUMMER.findall(zeile))
    return sorted(treffer)
'''

_PROMPT_DAUER = """\
Schreibe eine Funktion `parse_dauer(text: str) -> int`, die eine Dauerangabe in Sekunden umrechnet.

Die Eingabe besteht aus Einheiten `d` (Tage), `h` (Stunden), `m` (Minuten) und `s` (Sekunden),
jede höchstens einmal und nur in dieser Reihenfolge. Jede Zahl besteht aus den Ziffern 0 bis 9,
ohne Vorzeichen; Leerzeichen kommen nicht vor. Beispiele: "1h30m" ergibt 5400, "2d" ergibt 172800,
"1d2h3m4s" ergibt 93784.

Bei jeder anderen Eingabe — leere Zeichenkette, unbekannte Zeichen, fehlende oder doppelte
Einheiten, falsche Reihenfolge, Zahlen ohne Einheit, Leerzeichen — wirf `ValueError`.

Signatur (genau so übernehmen):

```python
def parse_dauer(text: str) -> int
```"""

_PROMPT_RATE = """\
Schreibe eine Klasse `RateLimiter`, die ein gleitendes Zeitfenster begrenzt.

`erlaubt()` ruft `uhr()` genau einmal je Aufruf auf (der Konstruktor nie) und liefert True, wenn im
Zeitraum `(jetzt - fenster_s, jetzt]` weniger als `max_aufrufe` erlaubte Aufrufe liegen. Erlaubte
Aufrufe werden gezählt, abgelehnte nicht. Der Konstruktor wirft `ValueError`, wenn
`max_aufrufe < 1` oder `fenster_s <= 0` ist.

Signatur (genau so übernehmen):

```python
from typing import Callable


class RateLimiter:
    def __init__(self, max_aufrufe: int, fenster_s: float, uhr: Callable[[], float]):
        ...

    def erlaubt(self) -> bool:
        ...
```"""

_PROMPT_WARTET = """\
Schreibe eine Funktion `parse_wartet_auf(text: str) -> list[int]`, die Abhängigkeiten aus einem
Issue-Text liest.

Zähle nur Zeilen, die nach optionalem Leerraum (Leerzeichen, Tabulatoren) mit `Wartet auf:`
beginnen (Groß-/Kleinschreibung egal), und darin alle Nummern der Form `#N`. Zeilen innerhalb von
Codeblöcken zählen nicht: eine Zeile, die nach optionalem Leerraum mit drei Backticks beginnt,
öffnet oder schließt einen Codeblock; ein offener Block reicht bis zum Textende. Zahlen ohne `#`
werden ignoriert. Das Ergebnis ist aufsteigend sortiert, ohne Dubletten; ohne Treffer `[]`.

Signatur (genau so übernehmen):

```python
def parse_wartet_auf(text: str) -> list[int]
```"""

CODE_TASKS: list[dict] = [
    {
        "id": "dauer_parsen",
        "titel": "Dauerangabe in Sekunden umrechnen",
        "prompt": _PROMPT_DAUER,
        "pruefung": _PRUEFUNG_DAUER,
        "referenz": _REFERENZ_DAUER,
    },
    {
        "id": "rate_limiter",
        "titel": "Rate-Limiter mit gleitendem Fenster",
        "prompt": _PROMPT_RATE,
        "pruefung": _PRUEFUNG_RATE,
        "referenz": _REFERENZ_RATE,
    },
    {
        "id": "wartet_auf",
        "titel": "Abhängigkeiten aus einem Issue-Text lesen",
        "prompt": _PROMPT_WARTET,
        "pruefung": _PRUEFUNG_WARTET,
        "referenz": _REFERENZ_WARTET,
    },
]

_OEFFNEN = re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_+.-]*)[ \t]*$")
_SCHLIESSEN = re.compile(r"^[ \t]*```[ \t]*$")


def build_code_prompt(task: dict) -> str:
    """`task["prompt"]` plus die feste Antwortvorgabe; Prüfung und Referenz bleiben draußen."""
    return f"{task['prompt'].strip()}\n\n{_ANTWORTVORGABE}"


def extract_code(antwort: str) -> str | None:
    """Inhalt des ersten passenden Python-Zauns; None ohne brauchbaren Block.

    Ein Zaun zählt nur, wenn ```python (Groß-/Kleinschreibung egal) oder gar keine Sprache
    angegeben ist, der Block geschlossen ist und sein Inhalt "def " oder "class " enthält. Zäune
    werden zeilenweise erkannt, damit drei Backticks mitten in einer Codezeile nichts beenden;
    ungezäunter Text, leere Eingabe, ein offener Block und Nicht-str ergeben None.
    """
    try:
        if not isinstance(antwort, str):
            return None
        zeilen = antwort.splitlines()
        index = 0
        while index < len(zeilen):
            oeffner = _OEFFNEN.match(zeilen[index])
            if oeffner is None:
                index += 1
                continue
            sprache = oeffner.group(1).lower()
            block: list[str] = []
            ende = index + 1
            while ende < len(zeilen) and _SCHLIESSEN.match(zeilen[ende]) is None:
                block.append(zeilen[ende])
                ende += 1
            if ende >= len(zeilen):
                return None
            if sprache in ("", "python"):
                inhalt = "\n".join(block)
                if "def " in inhalt or "class " in inhalt:
                    return inhalt
            index = ende + 1
    except Exception:
        return None
    return None


def _schreibe(pfad: str, inhalt: str) -> None:
    """Schreibt Quelltext verlustfrei genug: unpassende Zeichen werden ersetzt, nie geworfen."""
    with open(pfad, "w", encoding="utf-8", errors="replace", newline="\n") as datei:
        datei.write(inhalt)


def _pruefskript(pruefung: str, marker: str) -> str:
    """Vorspann (Netz-Sperre, sys.path), Prüfung und Nachspann mit dem Zufallsmarker."""
    return (
        _VORSPANN
        + "\n"
        + pruefung
        + "\n\n_sys.stdout.write("
        + repr(marker)
        + " + chr(10))\n_sys.stdout.flush()\n"
    )


def _grenzen(timeout_s: float) -> Callable[[], None] | None:
    """preexec_fn mit den festen Grenzen; CPU-Zeit ist eine Sekunde großzügiger als die Wanduhr."""
    if resource is None:  # pragma: no cover - Windows ist kein Zielsystem
        return None
    cpu_sekunden = int(math.ceil(timeout_s)) + 1

    def setze_grenzen() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (ADRESSRAUM_BYTES, ADRESSRAUM_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_sekunden, cpu_sekunden))
        resource.setrlimit(resource.RLIMIT_FSIZE, (DATEIGROESSE_BYTES, DATEIGROESSE_BYTES))

    return setze_grenzen


def _gruppe_toeten(pid: int) -> None:
    """Erschlägt die Prozessgruppe; eine bereits leere Gruppe ist kein Fehler."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _lies(pfad: str) -> bytes:
    with open(pfad, "rb") as datei:
        return datei.read()


def run_pruefung(code: str, pruefung: str, timeout_s: float = 10.0) -> tuple[bool, str]:
    """Führt `code` gegen `pruefung` in einem frischen, abgeriegelten Arbeitsverzeichnis aus.

    Rückgabe (True, "") bei Bestehen, sonst (False, Grund) mit einem der festen Gründe. Ausgaben
    des Modellcodes stehen nie im Grund; die Funktion wirft nie.
    """
    marker = secrets.token_hex(16)
    try:
        with tempfile.TemporaryDirectory(prefix="benchmark-code-") as wurzel:
            arbeit = os.path.join(wurzel, "arbeit")
            aus = os.path.join(wurzel, "aus")
            os.makedirs(arbeit)
            os.makedirs(aus)
            _schreibe(os.path.join(arbeit, "loesung.py"), code)
            _schreibe(os.path.join(arbeit, "pruefung.py"), _pruefskript(pruefung, marker))
            stdout_pfad = os.path.join(aus, "stdout")
            stderr_pfad = os.path.join(aus, "stderr")
            with open(stdout_pfad, "wb") as stdout_datei, open(stderr_pfad, "wb") as stderr_datei:
                prozess = subprocess.Popen(
                    [sys.executable, "-I", "pruefung.py"],
                    cwd=arbeit,
                    env={"PATH": "/usr/bin:/bin"},
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_datei,
                    stderr=stderr_datei,
                    start_new_session=True,
                    preexec_fn=_grenzen(timeout_s),
                )
            try:
                prozess.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _gruppe_toeten(prozess.pid)
                prozess.wait()
                return False, GRUND_ZEITLIMIT
            _gruppe_toeten(prozess.pid)
            if prozess.returncode != 0:
                if _NETZ_MARKER in _lies(stderr_pfad)[-_NETZ_FENSTER_BYTES:]:
                    return False, GRUND_NETZ
                return False, GRUND_FEHLGESCHLAGEN
            if not _lies(stdout_pfad).rstrip(b"\r\n").endswith(marker.encode("ascii")):
                return False, GRUND_FEHLGESCHLAGEN
            return True, ""
    except Exception:
        return False, GRUND_FEHLGESCHLAGEN


def score_code(task: dict, antwort: str, timeout_s: float = 10.0) -> dict:
    """Bewertet eine Modellantwort: {"bestanden": bool, "grund": str | None}; wirft nie."""
    try:
        code = extract_code(antwort)
        if code is None:
            return {"bestanden": False, "grund": GRUND_KEIN_CODE}
        bestanden, grund = run_pruefung(code, task["pruefung"], timeout_s)
    except Exception:
        return {"bestanden": False, "grund": GRUND_FEHLGESCHLAGEN}
    return {"bestanden": bestanden, "grund": None if bestanden else grund}
