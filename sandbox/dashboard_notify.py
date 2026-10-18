#!/usr/bin/env python3
"""Push-Meldungen des Dashboards über ntfy (löst die HA-Automation „PR braucht Stephan“ ab).

Der Dienst `dashboard.py` startet dafür einen Hintergrundfaden (`NotifyLoop`), der alle
NOTIFY_INTERVAL Sekunden den Stand prüft und bei folgenden Ereignissen eine Meldung schickt:

- neue Logzeile mit „⚠ Stephan“ (Worker-Fehler, PR geht an Stephan),
- ein Slot meldet „laufend“, aber sein Prozess lebt nicht mehr,
- ein LiteLLM-Key erreicht 90 % seines Limits (einmal je Key und Tag),
- GitHub-/Budgetdaten sind länger als 10 Minuten veraltet.

Eingerichtet wird es mit `NTFY_URL` (volle URL samt Topic, z. B. https://ntfy.sh/<Topic>; das
Topic ist das Geheimnis) und optional `NTFY_TOKEN` in `/etc/agent-dashboard.env` (Modus 600).
Fehlt `NTFY_URL`, passiert nichts. Weder URL noch Token erscheinen je in einer Ausgabe (Regel 5);
Fehler nennen nur die Art. Eine nicht zustellbare Meldung bleibt in der Warteschlange und wird
beim nächsten Durchlauf wiederholt. Test: `python dashboard.py --notify-test`.
"""
from __future__ import annotations

import base64
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

ENV_FILE: str = "/etc/agent-dashboard.env"   # systemd lädt sie; --notify-test liest sie selbst
STEPHAN_MARK = "⚠ Stephan"
BUDGET_PERCENT = 90        # ab diesem Anteil des Limits wird gemeldet
STALE_AFTER_S = 600        # so lange dürfen die GitHub-/Budgetdaten veraltet sein
MAX_QUEUE = 20             # mehr offene Meldungen werden verworfen (älteste zuerst)
MAX_LINES = 5              # Logzeilen je Meldung
MAX_BODY = 1000            # Zeichen je Meldung


@dataclass(frozen=True)
class Event:
    key: str
    title: str
    message: str
    priority: str = "default"   # ntfy: min, low, default, high, urgent
    tags: str = "warning"


def _header(text: str) -> str:
    """HTTP-Header sind ASCII; ntfy versteht sonst RFC 2047 (=?UTF-8?B?…?=)."""
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


class Notifier:
    """Schickt einen Event per POST an ntfy. `send` gibt nie URL oder Token aus."""

    def __init__(self, env: dict | None = None, opener=urllib.request.urlopen):
        env = os.environ if env is None else env
        url = env.get("NTFY_URL", "").strip()
        self.url = url if url.startswith(("http://", "https://")) else ""
        self.token = env.get("NTFY_TOKEN", "").strip()
        self.opener = opener
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def send(self, event: Event) -> bool:
        headers = {"Title": _header(event.title), "Priority": event.priority, "Tags": event.tags,
                   "Content-Type": "text/plain; charset=utf-8"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.url, data=event.message[:MAX_BODY].encode("utf-8"),
                                         headers=headers, method="POST")
        try:
            with self.opener(request, timeout=10) as resp:
                ok = 200 <= resp.status < 300
                self.last_error = "" if ok else f"HTTP {resp.status}"
                return ok
        except Exception as exc:   # noqa: BLE001 — nur die Art, nie der Text (enthält die URL)
            code = getattr(exc, "code", None)
            self.last_error = f"HTTP {code}" if code else type(exc).__name__
            if code in (301, 302, 303, 307, 308):
                host = _redirect_host(exc)
                if host:
                    self.last_error += f" → {host}"   # nur das Ziel, nie den Pfad (Regel 5)
            return False


def _redirect_host(exc: Exception) -> str:
    """Host (mit Port) aus einem absoluten `Location`-Header; leer, wenn er nicht brauchbar ist."""
    headers = getattr(exc, "headers", None) or getattr(exc, "hdrs", None)
    location = (headers.get("Location", "") if headers is not None else "") or ""
    parts = urllib.parse.urlsplit(location)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:   # ungültiger Port: dann lieber gar kein Ziel nennen
        return ""
    if port:
        host += f":{port}"
    return host


def read_env_file(path: str) -> dict[str, str]:
    """Liest KEY=WERT-Zeilen; fehlende oder unlesbare Datei ergibt {} (nie eine Ausnahme)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except (OSError, ValueError):
        return {}
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def notifier_for_test(path: str | None = None, environ: dict | None = None) -> Notifier:
    """Notifier für `--notify-test`: Umgebung und Datei, die Datei gewinnt bei gleichem Schlüssel.

    `path=None` liest `ENV_FILE` erst beim Aufruf, damit Tests die Konstante ersetzen können.
    """
    merged = dict(os.environ if environ is None else environ)
    merged.update(read_env_file(ENV_FILE if path is None else path))
    return Notifier(env=merged)


class Watcher:
    """Erkennt aus dem Dashboard-Stand, was gemeldet werden muss; merkt sich, was schon gemeldet ist."""

    def __init__(self, stale_after_s: float = STALE_AFTER_S, budget_percent: int = BUDGET_PERCENT):
        self.stale_after_s, self.budget_percent = stale_after_s, budget_percent
        self.dead: set[str] = set()
        self.budget_sent: set[tuple[str, str]] = set()
        self.stale_since: datetime | None = None
        self.stale_sent = False

    def events(self, state: dict, stephan_lines: list[str], now: datetime) -> list[Event]:
        out: list[Event] = []
        if stephan_lines:
            shown = stephan_lines[-MAX_LINES:]
            more = len(stephan_lines) - len(shown)
            body = "\n".join(shown) + (f"\n(+{more} weitere)" if more else "")
            out.append(Event("stephan", "⚠ Stephan: Agenten-Sandbox", body, "high", "rotating_light"))

        slots = {v["slot"]: v for v in state.get("slots", []) if v.get("tot")}
        for slot in sorted(set(slots) - self.dead):
            v = slots[slot]
            what = " ".join(x for x in (v.get("project"), f"Issue #{v['issue']}" if v.get("issue") else None,
                                        f"PR #{v['pr']}" if v.get("pr") else None) if x)
            out.append(Event(f"slot-{slot}", f"Slot {slot}: Prozess beendet",
                             "Der Takt ist abgestürzt, ohne sich abzumelden." + (f" Zuletzt: {what}." if what else ""),
                             "high", "skull"))
        self.dead = set(slots)

        today = now.date().isoformat()
        self.budget_sent = {k for k in self.budget_sent if k[1] == today}   # alte Tage vergessen
        for alias, b in sorted(state.get("budgets", {}).items()):
            percent = b.get("prozent")
            if percent is None or percent < self.budget_percent or (alias, today) in self.budget_sent:
                continue
            self.budget_sent.add((alias, today))
            out.append(Event(f"budget-{alias}", f"Budget {alias}: {percent} %",
                             f"${b.get('spend')} von ${b.get('max')} verbraucht.", "default", "moneybag"))

        if state.get("veraltet"):
            self.stale_since = self.stale_since or now
            if not self.stale_sent and (now - self.stale_since).total_seconds() >= self.stale_after_s:
                self.stale_sent = True
                out.append(Event("veraltet", "Dashboard: Daten veraltet",
                                 "GitHub oder LiteLLM sind seit über 10 Minuten nicht erreichbar.",
                                 "default", "hourglass"))
        else:
            self.stale_since, self.stale_sent = None, False
        return out


class NotifyLoop:
    """Prüft regelmäßig und stellt Meldungen zu; offene Meldungen bleiben bis zum Erfolg liegen."""

    def __init__(self, dash, notifier: Notifier, watcher: Watcher | None = None,
                 clock: Callable[[], datetime] = datetime.now):
        self.dash, self.notifier = dash, notifier
        self.watcher = watcher or Watcher()
        self.clock = clock
        self.queue: list[Event] = []
        self.seq = dash.logbuf.seq        # ältere Logzeilen werden nicht nachgemeldet
        self.failing = False

    def step(self) -> None:
        self.dash.logbuf.poll()
        new = self.dash.logbuf.since(self.seq)
        if new:
            self.seq = new[-1][0]
        lines = [ln for _, ln in new if STEPHAN_MARK in ln]
        self.queue += self.watcher.events(self.dash.state(), lines, self.clock())
        del self.queue[:-MAX_QUEUE]
        while self.queue:
            event = self.queue[0]
            if not self.notifier.send(event):
                if not self.failing:   # nur der Wechsel steht im Journal, nicht jeder Versuch
                    print(f"dashboard: Push fehlgeschlagen ({self.notifier.last_error}), wird wiederholt",
                          file=sys.stderr, flush=True)
                self.failing = True
                return
            self.queue.pop(0)
            self.failing = False
            print(f"dashboard: Push gesendet ({event.key})", flush=True)

    def run(self, interval: float) -> None:
        while True:
            try:
                self.step()
            except Exception as exc:   # noqa: BLE001 — der Faden darf nie sterben
                print(f"dashboard: Push-Durchlauf fehlgeschlagen ({type(exc).__name__})",
                      file=sys.stderr, flush=True)
            time.sleep(interval)


def send_test(notifier: Notifier) -> int:
    """`dashboard.py --notify-test`: 0 gesendet, 1 fehlgeschlagen, 2 nicht eingerichtet."""
    if not notifier.enabled:
        print("NTFY_URL fehlt oder ist ungültig (/etc/agent-dashboard.env)", file=sys.stderr)
        return 2
    if notifier.send(Event("test", "Agenten-Sandbox: Test", "Push-Meldungen funktionieren.", "default", "white_check_mark")):
        print("Testmeldung gesendet")
        return 0
    print(f"Testmeldung fehlgeschlagen ({notifier.last_error})", file=sys.stderr)
    return 1
