"""Hört auf einen ntfy-kompatiblen Strom und startet die Takt-Units sofort bei einer Nachricht."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Sequence


def is_wake_event(line: str) -> bool:
    """True nur, wenn `line` ein JSON-Objekt mit "event" == "message" ist.
    open, keepalive, kein JSON, leer, Liste, Zahl, Text: False. Der Nachrichteninhalt wird nie gelesen."""
    if not isinstance(line, str):
        return False
    try:
        data = json.loads(line)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("event") == "message"


def stream_url(value: str | None) -> str | None:
    """Getrimmte URL, wenn Schema http oder https und ein Host da sind, sonst None
    (auch bei None, leer, nacktem Themennamen, anderem Schema)."""
    if not value or not isinstance(value, str):
        return None
    val = value.strip()
    if not val:
        return None
    try:
        parsed = urllib.parse.urlsplit(val)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc or not parsed.hostname:
        return None
    return val


class Waker:
    def __init__(
        self,
        units: Sequence[str],
        min_gap: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.units = list(units)
        self.min_gap = float(min_gap)
        self.clock = clock
        self.runner = runner
        self._pending = False
        self._last_start: float | None = None

    def on_line(self, line: str) -> bool:
        """Ist `line` ein Wake-Ereignis, merken; danach poll(). True, wenn dabei gestartet wurde."""
        if is_wake_event(line):
            self._pending = True
        return self.poll()

    def poll(self) -> bool:
        """Startet, wenn ein Ereignis gemerkt ist und seit dem letzten Start mindestens `min_gap`
        Sekunden vergangen sind (beim ersten Mal sofort): runner(["systemctl", "start", "--no-block", *units]).
        Mehrere gemerkte Ereignisse ergeben einen Start (Nachlauf: ein Ereignis in der Sperrzeit geht
        beim nächsten poll() nach Ablauf raus). Ein Fehlversuch (Ausnahme oder Rückgabecode != 0)
        gibt False zurück, bleibt nicht als Ereignis liegen und schreibt eine Zeile auf stderr, die nur
        Fehlertyp bzw. Rückgabecode nennt, nie den Meldungstext. True nur bei Rückgabecode 0."""
        if not self._pending:
            return False

        now = self.clock()
        if self._last_start is not None and (now - self._last_start) < self.min_gap:
            return False

        self._pending = False
        cmd = ["systemctl", "start", "--no-block", *self.units]
        try:
            res = self.runner(cmd)
            rc = getattr(res, "returncode", 0)
            if rc != 0:
                sys.stderr.write(f"Fehler beim Starten der Units: Rückgabecode {rc}\n")
                return False
        except Exception as exc:
            sys.stderr.write(f"Fehler beim Starten der Units: {type(exc).__name__}\n")
            return False

        self._last_start = now
        return True


def run_once(url: str, waker: Waker, opener=urllib.request.urlopen) -> None:
    """Öffnet den Strom mit opener(Request, timeout=60), reicht jede Zeile (str, getrimmt) an
    waker.on_line, kehrt beim Ende des Stroms zurück. TimeoutError: still zurückkehren. Jede andere
    Ausnahme: eine Zeile auf stderr, die nur den Fehlertyp nennt (nie die URL, sie enthält das
    Geheimnis), dann zurückkehren. Wirft nie."""
    try:
        req = urllib.request.Request(url)
        with opener(req, timeout=60) as resp:
            for raw in resp:
                if isinstance(raw, bytes):
                    line = raw.decode("utf-8", errors="replace")
                else:
                    line = str(raw)
                waker.on_line(line.strip())
    except TimeoutError:
        return
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            return
        sys.stderr.write(f"Fehler im Wake-Stream: {type(exc).__name__}\n")
        return
    except Exception as exc:
        sys.stderr.write(f"Fehler im Wake-Stream: {type(exc).__name__}\n")
        return


def main(sleeper: Callable[[float], None] = time.sleep) -> None:
    """WAKE_URL aus der Umgebung (stream_url; ungültig: sys.exit mit Hinweis, ohne den Wert zu nennen),
    WAKE_UNITS (Leerzeichen getrennt, Pflicht), WAKE_MIN_GAP (Sekunden, Vorgabe 30). Schleife:
    run_once, waker.poll(), 5 s warten."""
    raw_url = os.environ.get("WAKE_URL")
    url = stream_url(raw_url)
    if not url:
        sys.stderr.write("Ungültige oder fehlende WAKE_URL\n")
        sys.exit(1)

    raw_units = os.environ.get("WAKE_UNITS", "").strip()
    if not raw_units:
        sys.stderr.write("Fehlende WAKE_UNITS\n")
        sys.exit(1)
    units = raw_units.split()

    raw_gap = os.environ.get("WAKE_MIN_GAP", "30")
    try:
        min_gap = float(raw_gap)
    except (ValueError, TypeError):
        min_gap = 30.0

    waker = Waker(units, min_gap=min_gap)
    while True:
        run_once(url, waker)
        waker.poll()
        sleeper(5)


if __name__ == "__main__":
    main()
