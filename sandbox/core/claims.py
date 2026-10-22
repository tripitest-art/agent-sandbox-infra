"""Dateisystem-basierte Auftrags-Belegung als eigenständiges Modul (#220).

Ein Auftrag ist belegt, solange `<claim_dir>/<skey>.claim` auf einen lebenden Prozess zeigt;
`<skey>.done` merkt an, dass irgendein Slot diese Nacharbeit schon als angekommen gemeldet hat
(#59). Beides stand vorher in `worker_tick.py`; hier kommt das Verzeichnis als Argument herein,
damit `worker_tick.py` und `review_tick.py` dieselbe Logik nutzen, ohne sie doppelt zu pflegen
(Regel 8, Phase-1 Modularisierung). Die PID ist ebenfalls Argument (Vorgabe: dieser Prozess) –
so prüfen Tests fremde Besitzer, ohne einen zweiten Prozess zu starten.

Keine Funktion wirft OSError: Ein nicht nutzbares Belegungsverzeichnis ist ein
Betriebszustand, den der Aufrufer meldet, statt den Takt stillzulegen (Regel 7).
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

_CLAIM_UNSAFE_RE = re.compile(r"[^A-Za-z0-9.@-]")
_WARNED: set[str] = set()          # Verzeichnisse, für die schon einmal gewarnt wurde


def _claim_name(skey: str) -> str:
    """Dateiname zu einem Auftragsschlüssel: unerlaubte Zeichen werden zu „_“."""
    return _CLAIM_UNSAFE_RE.sub("_", skey)


def _claim_path(skey: str, claim_dir: str | Path) -> Path:
    """Pfad der Belegungsdatei `<claim_dir>/<Name>.claim`; legt nichts an."""
    return Path(claim_dir) / (_claim_name(skey) + ".claim")


def _done_path(skey: str, claim_dir: str | Path) -> Path:
    """Pfad der „erledigt“-Marke `<claim_dir>/<Name>.done`; legt nichts an."""
    return Path(claim_dir) / (_claim_name(skey) + ".done")


def _eigentuemer(pid: int | None) -> int:
    """PID des Belegungs-Inhabers; ohne Angabe der laufende Prozess."""
    return os.getpid() if pid is None else pid


def _owner(path: Path) -> int | None:
    """PID aus einer Belegungsdatei; None, wenn sie fehlt, unlesbar oder keine Zahl ist."""
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int | None) -> bool:
    """True, wenn der Prozess noch läuft. Ein fehlendes Recht bedeutet: er lebt (anderer Nutzer)."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) ist unter Windows kein „nur prüfen“: Signal 0 ist dort CTRL_C_EVENT und
        # trifft die ganze Konsole samt pytest-Sitzung. Deshalb fragt der Windows-Zweig die
        # Win32-API, ohne os.kill (#118).
        import ctypes

        k = ctypes.windll.kernel32
        handle = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return k.GetLastError() == 5             # Zugriff verweigert: der Prozess lebt
        try:
            code = ctypes.c_ulong()
            k.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            k.CloseHandle(handle)                    # immer schließen, auch bei Fehler
        return code.value == 259                     # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _held_by_living_process(skey: str, claim_dir: str | Path, pid: int) -> bool:
    """True, wenn eine `.claim`-Datei einem anderen lebenden Prozess gehört."""
    besitzer = _owner(_claim_path(skey, claim_dir))
    return besitzer is not None and besitzer != pid and _pid_alive(besitzer)


def claim_job(skey: str, claim_dir: str | Path, pid: int | None = None) -> bool:
    """Beansprucht `skey` atomar für `pid` (Vorgabe: dieser Prozess) und meldet Erfolg.

    True: neu angelegt, schon eigener Besitz oder von einem beendeten Prozess übernommen.
    False: ein anderer lebender Prozess hält den Auftrag. Ohne die Belegung nehmen zwei Slots
    denselben Auftrag an (#59) – das kostet Modellläufe und MAX_TRIES. Angelegt wird die Datei
    mit O_EXCL, damit zwei gleichzeitige Slots nicht beide gewinnen; eine vorhandene Belegung
    ohne lesbare PID gilt als verwaist (abgebrochener Takt) und wird übernommen.

    Ist `claim_dir` nicht nutzbar, wird einmal eine „⚠ Stephan:“-Zeile nach stderr geschrieben
    (Regel 7) und True geliefert, damit der Takt nicht stillsteht. Dann ist Doppelbearbeitung
    wieder möglich – sichtbar, aber nicht verhindert.
    """
    eigentuemer = _eigentuemer(pid)
    try:
        ziel = Path(claim_dir)
        ziel.mkdir(parents=True, exist_ok=True)
        pfad = _claim_path(skey, ziel)
        try:
            fd = os.open(pfad, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if _held_by_living_process(skey, ziel, eigentuemer):
                return False
            # Eigene oder verwaiste Belegung (abgebrochener Takt): übernehmen.
            fd = os.open(pfad, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(eigentuemer))
        return True
    except OSError as exc:
        schluessel = str(claim_dir)
        if schluessel not in _WARNED:
            _WARNED.add(schluessel)
            print(f"⚠ Stephan: Auftrags-Belegung {schluessel} nicht nutzbar "
                  f"({type(exc).__name__}: {exc}) – Doppelbearbeitung wieder möglich.",
                  file=sys.stderr)
        return True


def release_job(skey: str, claim_dir: str | Path, pid: int | None = None) -> bool:
    """Gibt die Belegung frei, aber nur wenn sie `pid` gehört (Vorgabe: dieser Prozess).

    True: Datei gelöscht oder nicht vorhanden. False: fremder Besitzer (die Belegung des anderen
    Slots bleibt stehen) oder die Datei ließ sich nicht löschen. Eine fehlende oder unlesbare
    Datei gilt als frei – eine Belegung ohne lesbare PID schützt niemanden.
    """
    eigentuemer = _eigentuemer(pid)
    pfad = _claim_path(skey, claim_dir)
    besitzer = _owner(pfad)
    if besitzer is not None and besitzer != eigentuemer:
        return False
    try:
        pfad.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def mark_job_done(skey: str, claim_dir: str | Path, pid: int | None = None) -> None:
    """Merkt die angekommene Nacharbeit für alle Slots: Datei `<claim_dir>/<Name>.done`.

    Die Liste DONE in der Zustandsdatei bleibt je Slot; sie allein sah den anderen Slot nicht,
    und dieselbe Nacharbeit lief danach erneut an (#59). `claim_dir` entsteht bei Bedarf; ein
    Schreibfehler wird gemeldet, nicht geworfen.
    """
    try:
        ziel = Path(claim_dir)
        ziel.mkdir(parents=True, exist_ok=True)
        _done_path(skey, ziel).write_text(str(_eigentuemer(pid)), encoding="utf-8")
    except OSError as exc:
        print(f"Warnung: „erledigt“ für {skey} nicht merkbar ({type(exc).__name__}: {exc}).",
              file=sys.stderr)


def job_done_anywhere(skey: str, claim_dir: str | Path) -> bool:
    """True, wenn irgendein Slot diese Nacharbeit schon angekommen gemeldet hat (#59)."""
    return _done_path(skey, claim_dir).exists()


def cleanup_claims(claim_dir: str | Path, max_age_days: int = 30) -> int:
    """Löscht veraltete Dateien in `claim_dir` und gibt zurück, wie viele es waren.

    Gelöscht wird, was älter als `max_age_days` Tage ist, und zwar nur:
    - eine Datei mit Endung `.done`, immer;
    - eine Datei mit Endung `.claim`, wenn ihr Besitzer nicht mehr lebt: die PID in der Datei
      ist nicht lesbar oder `_pid_alive(pid)` ist False. Eine `.claim`-Datei eines lebenden
      Prozesses bleibt, egal wie alt sie ist.
    `max_age_days=0` nimmt alle vorhandenen Dateien ohne Altersfilter: direkt nach dem Lauf sind
    die eben geschriebenen Marken schon erledigt. Alles andere bleibt: Dateien mit anderer
    Endung, Unterverzeichnisse. Fehlt `claim_dir`, ist das Ergebnis 0 und das Verzeichnis wird
    nicht angelegt. Schlägt das Löschen einer Datei mit OSError fehl, wird sie übersprungen und
    nicht mitgezählt. Wirft nie.
    """
    grenze = time.time() - max_age_days * 86400
    entfernt = 0
    try:
        eintraege = list(os.scandir(claim_dir))
    except OSError:
        return 0
    for eintrag in eintraege:
        try:
            if not eintrag.is_file():
                continue
            if max_age_days > 0 and eintrag.stat().st_mtime >= grenze:
                continue
            if eintrag.name.endswith(".claim"):
                pid = _owner(Path(eintrag.path))
                if pid is not None and _pid_alive(pid):
                    continue
            elif not eintrag.name.endswith(".done"):
                continue
            os.remove(eintrag.path)
            entfernt += 1
        except OSError:
            continue            # gesperrt oder inzwischen weg: nicht zählen, Lauf nicht abbrechen
    return entfernt
