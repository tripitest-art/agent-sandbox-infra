#!/usr/bin/env python3
"""Web-Dashboard der Agenten-Sandbox (löst das Home-Assistant-Dashboard ab).

Nur lesend und nur Standardbibliothek. Der Dienst fasst vorhandene Quellen zusammen:
Slot-Status (`/run/worker-slots/slot-<n>.json`), Projekte,
Issues, PRs, „braucht-stephan“ und LiteLLM-Budgets (`build_status`) und den Schwanz
von `/root/worker.log`. Die Seite holt den Stand über Server-Sent-Events (`/events`).

Endpunkte: `/` (Seite), `/api/state` (JSON), `/events` (SSE), `/healthz`,
`POST /api/budget` (Limit eines LiteLLM-Keys ändern, nur mit Schreib-Token),
`POST /api/settings` (unkritische Einstellung in `agent_settings`-Datei ändern, nur mit
Schreib-Token) und `POST /api/wake` (Takt sofort über den Wake-Listener starten, nur mit
Schreib-Token).

Push-Meldungen über ntfy (`NTFY_URL`): siehe `dashboard_notify.py`.

Ausnahmen vom „nur lesend“: das Budget-Limit, die Einstellungsdatei und der Takt-Start. Für alle
drei braucht der Dienst `DASHBOARD_WRITE_TOKEN`; das Budget zusätzlich `LITELLM_MASTER_KEY`, das
Wecken `WAKE_PUBLISH_URL` (alle nur in `/etc/agent-dashboard.env`, Modus 600). Fehlt eines, ist die
Funktion aus und die Seite zeigt keinen Knopf. Geändert wird nur `max_budget` bzw. ein Wert aus der
festen Liste in `agent_settings.SPECS`; Env-Dateien und Units bleiben unberührt.

Kein Login: Der Dienst gehört ins LAN und nie hinter eine Portfreigabe. Deshalb liefert er
nur aufbereitete Felder; Fehler erscheinen als Name der Exception-Klasse, Log-Zeilen laufen
durch `redact` (Regel 5: Geheimnisse nie ausgeben).

Einstellungen (Umgebung): DASHBOARD_BIND (Standard 0.0.0.0), DASHBOARD_PORT (8099),
DASHBOARD_STATUS_TTL (Sekunden, in denen `gh`/LiteLLM-Antworten wiederverwendet werden, 90),
WORKER_LOG, SLOT_DIR, LITELLM sowie die Budget-Keys.
"""
from __future__ import annotations

import hmac
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # Module liegen nebenan
import agent_settings  # noqa: E402
import dashboard_notify  # noqa: E402
import dashboard_stats  # noqa: E402
import projects  # noqa: E402

if __name__ in sys.modules:
    sys.modules["dashboard"] = sys.modules[__name__]

LOG: str = os.environ.get("WORKER_LOG", "/root/worker.log")
SLOT_DIR: str = os.environ.get("SLOT_DIR", "/run/worker-slots")
BIND: str = os.environ.get("DASHBOARD_BIND", "0.0.0.0")
PORT: int = int(os.environ.get("DASHBOARD_PORT", "8099"))
STATUS_TTL: float = float(os.environ.get("DASHBOARD_STATUS_TTL", "90"))
NOTIFY_INTERVAL: float = float(os.environ.get("NOTIFY_INTERVAL", "30"))   # Sekunden zwischen zwei Push-Prüfungen
LOG_LINES = 400            # so viele Zeilen hält der Dienst vor
LOG_PRELOAD_BYTES = 64 * 1024
SSE_INTERVAL = 2.0         # Sekunden zwischen zwei Prüfungen je Verbindung
STEPHAN_MARK = "⚠ Stephan"
MIN_TOKEN_LEN = 16         # kürzere Schreib-Token gelten als nicht eingerichtet
MAX_BUDGET_LIMIT = 10000   # Obergrenze gegen Tippfehler (USD)
FAIL_DELAY = 1.0           # Sekunden Wartezeit nach falschem Token (bremst Raten)
WAKE_COOLDOWN = 10.0       # Sekunden zwischen zwei Takt-Starts über das Dashboard
DEAD_MESSAGE = "Prozess beendet"   # setzt read_slots für einen verwaisten Lauf

# Status- und Budget-Konstanten
STATES = ("bereit", "in-arbeit", "blockiert", "entwurf")
WAITING_LABEL = "braucht-stephan"
RED_CONCLUSIONS = frozenset({"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"})
BUDGET_KEYS = (("LITELLM_WORKER_KEY", "worker"),
               ("LITELLM_REVIEW_KEY", "reviewer"),
               ("LITELLM_PLANER_KEY", "planer"))
DEFAULT_LITELLM = "http://192.168.0.211:4000"

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Entfernt ANSI-Farbcodes und Escape-Sequenzen."""
    return _ANSI_ESCAPE_RE.sub("", text)


def pid_is_active(pid: int | None) -> bool:
    """Lebt der Prozess noch? False ohne Prozess-ID oder bei OSError (z. B. ESRCH)."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k = ctypes.windll.kernel32
        handle = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False                             # auch „Zugriff verweigert“ heißt hier: nicht aktiv
        try:
            code = ctypes.c_ulong()
            k.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            k.CloseHandle(handle)                    # immer schließen, auch bei Fehler
        return code.value == 259                     # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_slots(slot_dir: str = SLOT_DIR, is_pid_alive=pid_is_active) -> dict[str, dict]:
    """Alle slot-*.json aus slot_dir als {slot: daten}.

    Ein verwaister Slot (status „running“, aber PID tot) wird zu „idle“ mit
    message „Prozess beendet“: So zeigt das Dashboard keinen hängenden Lauf, wenn der
    Takt abstürzt, ohne sich abzumelden.
    """
    slots: dict[str, dict] = {}
    try:
        names = os.listdir(slot_dir)
    except OSError:
        return slots
    for name in names:
        m = re.match(r"^slot-(.+)\.json$", name)
        if not m:
            continue
        try:
            with open(os.path.join(slot_dir, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        slot = str(data.get("slot") or m.group(1))
        if data.get("status") == "running" and not is_pid_alive(data.get("pid")):
            data = {**data, "status": "idle", "message": DEAD_MESSAGE}
        slots[slot] = data
    return slots


class Follower:
    """Liest neu angehängte Zeilen einer Datei; erkennt Abschneiden und Ersetzen."""

    def __init__(self, path: str, from_start: bool = False):
        self.path, self.fh, self.inode, self.partial = path, None, None, ""
        self._open(from_start)

    def _open(self, from_start: bool) -> None:
        if self.fh:
            self.fh.close()
        try:
            self.fh = open(self.path, encoding="utf-8", errors="replace")
        except OSError:
            self.fh, self.inode = None, None
            return
        self.inode = os.fstat(self.fh.fileno()).st_ino
        if not from_start:
            self.fh.seek(0, os.SEEK_END)

    def poll(self) -> list[str]:
        """Neue, vollständige Zeilen seit dem letzten Aufruf (ohne Zeilenende)."""
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if self.fh is None or st.st_ino != self.inode:
            self._open(from_start=True)          # ersetzt (z. B. rotiert) -> neue Datei von vorn
        elif st.st_size < self.fh.tell():
            self.fh.seek(0)                      # abgeschnitten -> von vorn
            self.partial = ""
        if self.fh is None:
            return []
        data = self.partial + self.fh.read()
        lines = data.split("\n")
        self.partial = lines.pop()               # letzte Zeile ohne \n: noch unvollständig
        return [ln for ln in lines if ln.strip()]


# Umgebungsvariablen, deren Werte nie im Dashboard erscheinen dürfen.
_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|WEBHOOK|URL)", re.IGNORECASE)


def redact(text: str, env: dict | None = None) -> str:
    """ANSI-Codes entfernen und die Werte geheimer Umgebungsvariablen maskieren."""
    text = strip_ansi(text)
    for name, value in (os.environ if env is None else env).items():
        if len(value) >= 8 and _SECRET_NAME_RE.search(name):
            text = text.replace(value, f"<{name}>")
    return text


class LogBuffer:
    """Hält die letzten Log-Zeilen mit laufender Nummer; liest neue Zeilen nach."""

    def __init__(self, path: str = LOG, limit: int = LOG_LINES, stats=None):
        self.path = path
        self.stats = stats
        self.lines: deque[tuple[int, str]] = deque(maxlen=limit)
        self.seq = 0
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._add(self._preload())
        self._follower = Follower(path)   # ab dem aktuellen Dateiende
        if stats is not None and self._follower.fh:
            stats.preload(path, self._follower.fh.tell())   # Statistik: weiter zurück, bis genau hierher

    def _preload(self) -> list[str]:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - LOG_PRELOAD_BYTES))
                text = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        lines = [ln.rstrip("\r") for ln in text.split("\n")]
        if len(text) >= LOG_PRELOAD_BYTES:
            lines = lines[1:]   # erste Zeile ist angeschnitten
        return [ln for ln in lines if ln.strip()]

    def _add(self, new: list[str]) -> None:
        with self._lock:
            for ln in new:
                self.seq += 1
                self.lines.append((self.seq, redact(ln)))

    def poll(self) -> None:
        with self._poll_lock:   # mehrere SSE-Verbindungen teilen sich einen Follower
            new = self._follower.poll()
            if self.stats is not None:
                for ln in new:
                    self.stats.feed(ln)
            self._add(new)

    def since(self, seq: int) -> list[tuple[int, str]]:
        with self._lock:
            return [(n, ln) for n, ln in self.lines if n > seq]

    def stephan(self, limit: int = 10) -> list[str]:
        with self._lock:
            hits = [ln for _, ln in self.lines if STEPHAN_MARK in ln]
        return hits[-limit:]


class StatusCache:
    """Merkt sich den teuren Teil (gh, LiteLLM) für `ttl` Sekunden.

    Scheitert ein Neuladen als Ganzes, bleibt der alte Stand stehen und `veraltet` wird
    gesetzt; die Seite zeigt dann einen Hinweis statt eines leeren Bildschirms.
    """

    def __init__(self, builder: Callable[[], dict], ttl: float = STATUS_TTL,
                 clock: Callable[[], float] = time.monotonic):
        self.builder, self.ttl, self.clock = builder, ttl, clock
        self.value: dict | None = None
        self.loaded_at = 0.0
        self.veraltet = False
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        """Beim nächsten `get` neu laden (z. B. nach einer Budget-Änderung)."""
        with self._lock:
            self.loaded_at = float("-inf")

    def get(self) -> tuple[dict | None, bool]:
        with self._lock:
            if self.value is None or self.clock() - self.loaded_at >= self.ttl:
                try:
                    self.value, self.veraltet = self.builder(), False
                except Exception:   # noqa: BLE001 — Dashboard bleibt bedienbar, Fehlerart steht nirgends
                    self.veraltet = True
                self.loaded_at = self.clock()
            return self.value, self.veraltet


class BudgetError(Exception):
    """Fehler beim Ändern eines Budgets; die Meldung ist für die Anzeige gedacht (nie Geheimnisse)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _labels(obj: dict) -> set[str]:
    """Labelnamen eines Issues/PRs aus dem `gh --json`-Format (Labels als [{"name": …}])."""
    return {lab.get("name") for lab in obj.get("labels") or [] if isinstance(lab, dict)}


def _pr_state(pr: dict) -> str:
    """„rot“, „laufend“ oder „gruen“ aus statusCheckRollup. Fehlt/leer: weder rot noch laufend."""
    running = False
    for check in pr.get("statusCheckRollup") or []:
        if check.get("conclusion") in RED_CONCLUSIONS:
            return "rot"
        if check.get("status") != "COMPLETED":
            running = True
    return "laufend" if running else "gruen"


def summarize_project(p: projects.Project, issues: list[dict], prs: list[dict]) -> dict:
    """Zustand eines Projekts: Issues je Zustandslabel, PR-Zahlen und wartende PRs."""
    counts = {state: 0 for state in STATES}
    for issue in issues:
        for state in STATES:
            if state in _labels(issue):
                counts[state] += 1
    rot = laufend = 0
    waiting: list[dict] = []
    for pr in prs:
        state = _pr_state(pr)
        if state == "rot":
            rot += 1
        elif state == "laufend":
            laufend += 1
        if WAITING_LABEL in _labels(pr):
            waiting.append({"number": pr["number"], "title": pr["title"], "url": pr["url"]})
    waiting.sort(key=lambda e: e["number"])
    return {
        "name": p.name,
        "repo": p.repo,
        "issues": counts,
        "prs": {"offen": len(prs), "rot": rot, "laufend": laufend, "wartet_auf_stephan": waiting},
    }


def summarize_budget(info: dict | None) -> dict:
    """LiteLLM-Budget eines Keys: spend, max_budget und Prozent. None = nicht erreichbar."""
    if info is None:
        return {"fehler": "nicht erreichbar"}
    spend = info.get("spend") or 0.0
    max_budget = info.get("max_budget")
    prozent = round(spend / max_budget * 100) if max_budget else None
    return {"spend": round(spend, 2), "max": max_budget, "prozent": prozent}


def build_status(items: list[projects.Project], fetch: Callable[[str, str], list[dict]],
                 budgets: dict[str, dict | None], now: datetime) -> dict:
    """Gesamtstatus: je Projekt ein summarize_project, die Summe der wartenden PRs und die Budgets.

    Wirft `fetch` für ein Projekt eine Exception, bekommt nur dieses Projekt einen
    `fehler`-Eintrag (Name der Klasse, nie der Meldungstext); die anderen laufen weiter.
    """
    projekte: list[dict] = []
    braucht_stephan = 0
    for p in items:
        try:
            issues = fetch("issues", p.repo)
            prs = fetch("prs", p.repo)
        except Exception as exc:   # noqa: BLE001 — ein kaputtes Projekt darf die anderen nicht stoppen
            projekte.append({"name": p.name, "repo": p.repo, "fehler": type(exc).__name__})
            continue
        summary = summarize_project(p, issues, prs)
        projekte.append(summary)
        braucht_stephan += len(summary["prs"]["wartet_auf_stephan"])
    return {
        "zeit": now.isoformat(timespec="seconds"),
        "projekte": projekte,
        "braucht_stephan": braucht_stephan,
        "budgets": {alias: summarize_budget(info) for alias, info in budgets.items()},
    }


def _gh_fetch(kind: str, repo: str) -> list[dict]:
    """Offene Issues (Label worker-task) bzw. PRs eines Repos über `gh` — nur lesend."""
    if kind == "issues":
        args = ["issue", "list", "--repo", repo, "--state", "open", "--label", "worker-task",
                "--json", "number,title,labels", "--limit", "200"]
    else:
        args = ["pr", "list", "--repo", repo, "--state", "open",
                "--json", "number,title,url,isDraft,labels,statusCheckRollup", "--limit", "100"]
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    text = out.stdout.strip()
    return json.loads(text) if text else []


def _default_key_info(key: str, base: str) -> dict | None:
    """`GET {base}/key/info` mit dem Key; das Feld `info` oder None bei jedem Fehler."""
    request = urllib.request.Request(f"{base.rstrip('/')}/key/info",
                                     headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:   # noqa: BLE001 — nicht erreichbar/kein JSON zählt als None
        return None
    info = data.get("info")
    return info if isinstance(info, dict) else None


class BudgetWriter:
    """Setzt `max_budget` eines LiteLLM-Keys über `POST /key/update` mit dem Master-Key.

    Ohne Master-Key und ausreichend langen Schreib-Token ist `enabled` falsch und nichts
    lässt sich ändern. Fehlertexte nennen nur Status und Art, nie Keys, Token oder URLs.
    """

    def __init__(self, env: dict | None = None, update: Callable[[str, float], None] | None = None):
        env = dict(os.environ if env is None else env)
        self.token = env.get("DASHBOARD_WRITE_TOKEN", "")
        self.master = env.get("LITELLM_MASTER_KEY", "")
        self.base = env.get("LITELLM") or DEFAULT_LITELLM
        self.keys = {alias: env[var] for var, alias in BUDGET_KEYS if env.get(var)}
        self.update = update or self._litellm_update
        self.fail_delay = FAIL_DELAY

    @property
    def enabled(self) -> bool:
        return len(self.token) >= MIN_TOKEN_LEN and bool(self.master)

    def token_ok(self, supplied: str) -> bool:
        return self.enabled and hmac.compare_digest(supplied.encode("utf-8"), self.token.encode("utf-8"))

    def _litellm_update(self, key: str, max_budget: float) -> None:
        body = json.dumps({"key": key, "max_budget": max_budget}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base.rstrip('/')}/key/update", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.master}", "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=10).close()
        except urllib.error.HTTPError as exc:
            raise BudgetError(f"LiteLLM lehnt die Änderung ab (HTTP {exc.code})", 502) from None
        except Exception as exc:   # noqa: BLE001 — nur die Art des Fehlers, nie der Text (URL, Key)
            raise BudgetError(f"LiteLLM nicht erreichbar ({type(exc).__name__})", 502) from None

    def set_budget(self, alias: object, value: object) -> float:
        """Prüft Eingaben und setzt das Limit; Rückgabe ist der gesetzte Wert."""
        if not isinstance(alias, str) or alias not in self.keys:
            raise BudgetError("Unbekannter Key")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise BudgetError("Limit muss eine Zahl sein")
        if not 0 < value <= MAX_BUDGET_LIMIT:
            raise BudgetError(f"Limit muss größer 0 und höchstens {MAX_BUDGET_LIMIT} sein")
        value = round(float(value), 2)
        self.update(self.keys[alias], value)
        return value


class SettingsWriter:
    """Schreibt einen Wert aus der festen Liste in die Einstellungsdatei (`agent_settings`).

    Der Schreib-Token genügt; ein Master-Key ist nicht nötig. Geschrieben wird nur die Datei,
    nie eine Env-Datei oder Unit. Fehlertexte nennen weder Token noch Pfad.
    """

    def __init__(self, env: dict | None = None):
        env = dict(os.environ if env is None else env)
        self.token = env.get("DASHBOARD_WRITE_TOKEN", "")
        self.fail_delay = FAIL_DELAY

    @property
    def enabled(self) -> bool:
        return len(self.token) >= MIN_TOKEN_LEN

    def token_ok(self, supplied: str) -> bool:
        return self.enabled and hmac.compare_digest(supplied.encode("utf-8"), self.token.encode("utf-8"))

    def set_value(self, name: object, value: object):
        """Prüft Name und Wert und ergänzt die Datei; Rückgabe ist der gesetzte Wert."""
        try:
            agent_settings.validate(name, value)
            agent_settings.save({name: value})
        except agent_settings.SettingError as exc:
            raise BudgetError(str(exc)) from None
        except OSError as exc:
            raise BudgetError(f"Einstellungsdatei nicht schreibbar ({type(exc).__name__})", 500) from None
        return value


class WakeError(Exception):
    """Fehler beim Wecken; die Meldung ist für die Anzeige gedacht (nie URL oder Token)."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class WakeSender:
    """Schickt das Wort „wake“ per POST an `WAKE_PUBLISH_URL` (ntfy).

    Der Wake-Listener (`wake_listener.py`, Dienst `agent-wake`) startet bei jeder Nachricht
    im Thema die Takt-Units; den Inhalt liest er nicht. Damit ersetzt der Knopf den früheren
    Home-Assistant-Knopf, ohne dass das Dashboard `systemctl` aufruft. Fehlertexte nennen
    nie URL, Thema oder Token (Regel 5).
    """

    def __init__(self, env: dict | None = None, publish: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        env = dict(os.environ if env is None else env)
        self.token = env.get("DASHBOARD_WRITE_TOKEN", "")
        self.url = env.get("WAKE_PUBLISH_URL", "")
        self.publish = publish or self._post_wake
        self.clock = clock
        self.fail_delay = FAIL_DELAY
        self.sent_at: float | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """True nur mit Token der Länge >= MIN_TOKEN_LEN und einer URL mit Schema http oder https und Host."""
        if len(self.token) < MIN_TOKEN_LEN:
            return False
        parts = urllib.parse.urlsplit(self.url)
        return parts.scheme in ("http", "https") and bool(parts.hostname)

    def token_ok(self, supplied: str) -> bool:
        """Konstantzeit-Vergleich mit dem Schreib-Token; False, wenn nicht `enabled`."""
        return self.enabled and hmac.compare_digest(supplied.encode("utf-8"), self.token.encode("utf-8"))

    def _post_wake(self, url: str) -> None:
        request = urllib.request.Request(url, data=b"wake", method="POST")
        urllib.request.urlopen(request, timeout=10).close()

    def send(self) -> None:
        """Sendet „wake“; höchstens einmal je WAKE_COOLDOWN Sekunden, sonst WakeError(429)."""
        with self._lock:
            now = self.clock()
            if self.sent_at is not None and now - self.sent_at < WAKE_COOLDOWN:
                raise WakeError("Wecken höchstens einmal je 10 Sekunden", 429)
            try:
                self.publish(self.url)
            except WakeError:
                raise
            except urllib.error.HTTPError as exc:
                raise WakeError(f"ntfy lehnt ab (HTTP {exc.code})") from None
            except Exception as exc:   # noqa: BLE001 — nur die Art des Fehlers, nie der Text (URL, Token)
                raise WakeError(f"Wecken nicht möglich ({type(exc).__name__})") from None
            self.sent_at = now


def default_builder(env: dict | None = None) -> dict:
    """Projekte, Issues, PRs und Budgets."""
    env = dict(os.environ if env is None else env)
    base = env.get("LITELLM") or DEFAULT_LITELLM
    budgets = {}
    for var, alias in BUDGET_KEYS:
        if env.get(var):
            budgets[alias] = _default_key_info(env[var], base)
    return build_status(projects.load_projects(env=env), _gh_fetch,
                        budgets, datetime.now(timezone.utc))


def _elapsed(started_at: str | None, now: datetime) -> int | None:
    """Laufzeit in Sekunden aus „HH:MM:SS“ (der Slot kennt nur die Uhrzeit, kein Datum)."""
    try:
        h, m, s = (int(x) for x in (started_at or "").split(":"))
    except ValueError:
        return None
    secs = (now.hour * 3600 + now.minute * 60 + now.second) - (h * 3600 + m * 60 + s)
    return secs if secs >= 0 else secs + 86400   # über Mitternacht


def slot_view(slot: str, data: dict, now: datetime) -> dict:
    """Ein Slot für die Seite: nur bekannte Felder, Laufzeit berechnet, Status vereinheitlicht."""
    status = data.get("status") if data.get("status") in ("running", "idle") else "unbekannt"
    view = {"slot": slot, "status": status,
            "tot": status == "idle" and data.get("message") == DEAD_MESSAGE}
    for key in ("message", "issue", "pr", "title", "branch", "model", "agent", "project",
                "started_at", "updated_at"):
        if data.get(key) is not None:
            view[key] = data[key]
    if status == "running":
        view["laufzeit_s"] = _elapsed(data.get("started_at"), now)
    return view


def _slot_sort(item: tuple[str, dict]) -> tuple[int, str]:
    slot = item[0]
    return (0, f"{int(slot):06d}") if slot.isdigit() else (1, slot)


def build_state(slots: dict[str, dict], status: dict | None, veraltet: bool,
                stephan_lines: list[str], now: datetime) -> dict:
    """Gesamtstand für `/api/state` und die SSE-Ereignisse (ohne Log-Zeilen)."""
    views = [slot_view(s, d, now) for s, d in sorted(slots.items(), key=_slot_sort)]
    projekte = (status or {}).get("projekte", [])
    issues = sum(sum(p.get("issues", {}).values()) for p in projekte)
    prs = sum(p.get("prs", {}).get("offen", 0) for p in projekte)
    alerts = [{"art": "slot", "text": f"Slot {v['slot']}: Prozess beendet, ohne sich abzumelden"}
              for v in views if v["tot"]]
    alerts += [{"art": "stephan", "text": ln} for ln in stephan_lines]
    return {
        "zeit": now.isoformat(timespec="seconds"),
        "slots": views,
        "kopf": {"aktive_slots": sum(v["status"] == "running" for v in views),
                 "offene_issues": issues, "offene_prs": prs},
        "projekte": projekte,
        "braucht_stephan": (status or {}).get("braucht_stephan", 0),
        "budgets": (status or {}).get("budgets", {}),
        "stand": (status or {}).get("zeit"),
        "veraltet": veraltet or status is None,
        "alerts": alerts,
    }


class Dashboard:
    """Bündelt Quellen und liefert Zustand sowie SSE-Rahmen; testbar ohne HTTP."""

    def __init__(self, cache: StatusCache, logbuf: LogBuffer, slot_dir: str = SLOT_DIR,
                 is_pid_alive=pid_is_active,
                 clock: Callable[[], datetime] = lambda: datetime.now(),
                 writer: BudgetWriter | None = None, waker: WakeSender | None = None):
        self.cache, self.logbuf, self.slot_dir = cache, logbuf, slot_dir
        self.is_pid_alive, self.clock = is_pid_alive, clock
        self.writer = writer or BudgetWriter(env={})   # ohne Angabe: Ändern aus
        self.waker = waker or WakeSender(env={})       # ohne Angabe: Wecken aus
        self.settings_writer = SettingsWriter()        # ohne Token in der Umgebung: Ändern aus

    def set_budget(self, alias: object, value: object) -> float:
        new = self.writer.set_budget(alias, value)
        self.cache.invalidate()   # die Anzeige holt den neuen Stand sofort
        return new

    def state(self) -> dict:
        status, veraltet = self.cache.get()
        slots = read_slots(self.slot_dir, self.is_pid_alive)
        state = build_state(slots, status, veraltet, self.logbuf.stephan(), self.clock())
        state["schreiben"] = self.writer.enabled   # zeigt den „Limit ändern“-Knopf
        state["wecken"] = self.waker.enabled       # zeigt den Knopf „Takt starten“
        state["einstellungen"] = {
            "schreiben": self.settings_writer.enabled,
            "werte": agent_settings.aktuelle_werte(),
            "spezifikation": {name: {k: v for k, v in spec.items()
                                     if k in ("text", "min", "max", "erlaubt", "vorgabe")}
                              for name, spec in agent_settings.SPECS.items()},
        }
        stats = self.logbuf.stats
        state["statistik"] = stats.snapshot(self.clock()) if stats is not None else None
        return state

    def frames(self, last_seq: int, last_state_json: str | None):
        """Neue SSE-Rahmen seit dem letzten Stand: (Rahmen, last_seq, last_state_json)."""
        out: list[str] = []
        state = self.state()
        # `zeit` ändert sich jede Sekunde und löst allein kein Ereignis aus; die Laufzeit schon.
        cmp = json.dumps({k: v for k, v in state.items() if k != "zeit"}, ensure_ascii=False,
                         sort_keys=True)
        if cmp != last_state_json:
            out.append(sse("state", state))
            last_state_json = cmp
        new = self.logbuf.since(last_seq)
        if new:
            out.append(sse("log", [ln for _, ln in new]))
            last_seq = new[-1][0]
        return out, last_seq, last_state_json


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


PAGE = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agenten-Sandbox</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2128;--mut:#6b7480;--line:#dde1e6;--blue:#1f6feb;
--grn:#1a7f37;--red:#cf222e;--yel:#9a6700;--gry:#8c959f}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;--mut:#8b949e;
--line:#30363d;--blue:#58a6ff;--grn:#3fb950;--red:#f85149;--yel:#d29922;--gry:#8b949e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,sans-serif;padding:16px}
h1{font-size:18px;margin:0 0 12px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;
color:var(--mut);margin:20px 0 8px}
.kopf{display:flex;flex-wrap:wrap;gap:8px}.kpi{background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:8px 12px;min-width:110px}.kpi b{display:block;font-size:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;color:#fff;background:var(--gry)}
.running{background:var(--blue)}.idle{background:var(--gry)}.tot{background:var(--red)}
.mut{color:var(--mut)}.banner{background:var(--red);color:#fff;padding:8px 12px;border-radius:8px;margin:8px 0}
.warn{background:var(--yel);color:#fff;padding:6px 12px;border-radius:8px;margin:8px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}
td,th{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:4px}
.bar i{display:block;height:100%;background:var(--grn)}.bar i.hi{background:var(--red)}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px;height:320px;
overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:12px;margin:0}
.st{color:var(--red);font-weight:600}a{color:var(--blue)}
.table-wrap{overflow-x:auto}
button{font:inherit;font-size:12px;padding:1px 8px;border:1px solid var(--line);border-radius:6px;
background:var(--card);color:var(--fg);cursor:pointer}
</style></head><body>
<h1>Agenten-Sandbox <span id="conn" class="mut"></span> <span id="wake"></span></h1>
<div id="alerts"></div><div id="veraltet"></div>
<div class="kopf" id="kopf"></div>
<h2>Worker-Slots</h2><div class="grid" id="slots"></div>
<h2>Braucht Stephan</h2><div id="stephan"></div>
<h2>Projekte</h2><div class="table-wrap"><table id="projekte"></table></div>
<h2>Reviews und Merges</h2><div class="table-wrap"><table id="statistik"></table></div>
<h2>LiteLLM-Budgets</h2><div class="grid" id="budgets"></div>
<h2>Einstellungen <span class="mut">gilt ab dem nächsten Takt</span></h2>
<div class="table-wrap"><table id="einstellungen"></table></div>
<h2>Log <label class="mut"><input type="checkbox" id="nur"> nur ⚠ Stephan</label></h2>
<pre id="log"></pre>
<script>
const $=id=>document.getElementById(id);
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e};
const fmt=s=>s==null?"":(s>=3600?Math.floor(s/3600)+" h ":"")+Math.floor(s%3600/60)+" min";
let logLines=[];
function renderStat(st){const t=$("statistik");t.replaceChildren();if(!st)return;
 const h=el("tr");["","Freigaben","Änderungen nötig","Quote","Gemergt","Merge-Fehler","An Stephan","Worker-Fehler"].forEach(x=>h.append(el("th",null,x)));t.append(h);
 const row=(name,c)=>{const r=el("tr");r.append(el("td",null,name));
  [c.freigegeben,c.aenderungen,c.quote==null?"–":c.quote+" %",c.gemergt,c.merge_fehler,c.an_stephan,c.worker_fehler].forEach(x=>r.append(el("td",null,x)));t.append(r)};
 row("24 Stunden",st["24h"]);row("7 Tage",st["7d"]);
 Object.entries(st.projekte).forEach(([n,c])=>row("7 Tage · "+n,c));
 Object.entries(st.modelle).forEach(([n,c])=>row("7 Tage · Reviewer "+n,c))}
async function setBudget(alias,cur){
 const v=prompt("Neues Limit für "+alias+" in USD (jetzt "+cur+"):",cur);if(v===null)return;
 const num=Number(v.replace(",","."));if(!isFinite(num)||v.trim()===""){alert("Keine Zahl");return}
 let tok="";try{tok=sessionStorage.getItem("wt")||""}catch(e){}
 if(!tok){tok=prompt("Schreib-Token:")||"";if(!tok)return}
 const r=await fetch("api/budget",{method:"POST",headers:{"Content-Type":"application/json","X-Dashboard-Token":tok},
  body:JSON.stringify({alias:alias,max_budget:num})});
 const j=await r.json().catch(()=>({}));
 if(r.ok){try{sessionStorage.setItem("wt",tok)}catch(e){}alert("Limit von "+alias+" auf $"+j.max_budget+" gesetzt")}
 else{if(r.status===403){try{sessionStorage.removeItem("wt")}catch(e){}}alert(j.fehler||"Fehler "+r.status)}
}
async function wecken(){
 let tok="";try{tok=sessionStorage.getItem("wt")||""}catch(e){}
 if(!tok){tok=prompt("Schreib-Token:")||"";if(!tok)return}
 const r=await fetch("api/wake",{method:"POST",headers:{"X-Dashboard-Token":tok}});
 const j=await r.json().catch(()=>({}));
 if(r.ok){try{sessionStorage.setItem("wt",tok)}catch(e){}alert("Takt gestartet")}
 else{if(r.status===403){try{sessionStorage.removeItem("wt")}catch(e){}}alert(j.fehler||"Fehler "+r.status)}
}
async function setSetting(name,cur,erlaubt){
 const v=prompt("Neuer Wert für "+name+":",cur==null?"":cur);if(v===null)return;
 let wert=v.trim();
 if(!erlaubt){const num=Number(wert.replace(",","."));
  if(wert===""||!isFinite(num)||!Number.isInteger(num)){alert("Wert muss eine ganze Zahl sein");return}
  wert=num}
 let tok="";try{tok=sessionStorage.getItem("wt")||""}catch(e){}
 if(!tok){tok=prompt("Schreib-Token:")||"";if(!tok)return}
 const r=await fetch("api/settings",{method:"POST",headers:{"Content-Type":"application/json","X-Dashboard-Token":tok},
  body:JSON.stringify({name:name,value:wert})});
 const j=await r.json().catch(()=>({}));
 if(r.ok){try{sessionStorage.setItem("wt",tok)}catch(e){}alert("Einstellung "+j.name+" auf "+j.value+" gesetzt")}
 else{if(r.status===403){try{sessionStorage.removeItem("wt")}catch(e){}}alert(j.fehler||"Fehler "+r.status)}
}
function renderEinstellungen(e){
 const t=$("einstellungen");t.replaceChildren();
 const h=el("tr");["Name","Beschreibung","Wert","Quelle",""].forEach(x=>h.append(el("th",null,x)));t.append(h);
 Object.keys(e.werte).sort().forEach(n=>{const w=e.werte[n],sp=e.spezifikation[n]||{},r=el("tr");
  r.append(el("td",null,n),el("td",null,sp.text||""),el("td",null,w.wert==null?"–":w.wert),el("td",null,w.quelle));
  const c=el("td");
  if(e.schreiben){const b=el("button",null,"Ändern");b.onclick=()=>setSetting(n,w.wert,!!sp.erlaubt);c.append(b)}
  r.append(c);t.append(r)})}
function renderWake(s){const b=$("wake");b.replaceChildren();if(!s.wecken)return;const t=el("button",null,"Takt starten");t.onclick=wecken;b.append(t)}
function renderLog(){const l=$("log"),nur=$("nur").checked,
 stick=l.scrollTop+l.clientHeight>=l.scrollHeight-20;
 l.textContent=logLines.filter(x=>!nur||x.includes("⚠ Stephan")).join("\n");
 if(stick)l.scrollTop=l.scrollHeight}
$("nur").onchange=renderLog;
function render(s){
 const al=$("alerts");al.replaceChildren(...s.alerts.map(a=>el("div","banner",a.text)));
 $("veraltet").replaceChildren(...(s.veraltet?[el("div","warn","GitHub-/Budgetdaten veraltet oder nicht erreichbar")]:[]));
 $("kopf").replaceChildren(...[["Aktive Slots",s.kopf.aktive_slots],["Offene Issues",s.kopf.offene_issues],
  ["Offene PRs",s.kopf.offene_prs],["Stand",(s.stand||"–").slice(11,19)]].map(([k,v])=>{
  const d=el("div","kpi");d.append(el("span","mut",k),el("b",null,v));return d}));
 renderWake(s);
 $("slots").replaceChildren(...s.slots.map(v=>{const d=el("div","card");
  d.append(el("span","badge "+(v.tot?"tot":v.status),v.tot?"tot":v.status),el("b",null," Slot "+v.slot));
  const rows=[v.project,v.model,v.branch,v.message];
  if(v.issue)rows.push("Issue #"+v.issue);if(v.pr)rows.push("PR #"+v.pr);
  if(v.laufzeit_s!=null)rows.push("Laufzeit "+fmt(v.laufzeit_s));
  rows.filter(Boolean).forEach(r=>d.append(el("div","mut",r)));return d}));
 const st=s.projekte.flatMap(p=>(p.prs?p.prs.wartet_auf_stephan:[]).map(x=>({...x,projekt:p.name})));
 $("stephan").replaceChildren(...(st.length?st.map(x=>{const d=el("div","card");
  const a=el("a",null,"#"+x.number+" "+x.title);a.href=x.url;a.rel="noopener";
  d.append(el("span","mut",x.projekt+" · "),a);return d}):[el("span","mut","Nichts offen")]));
 const t=$("projekte");t.replaceChildren();
 const h=el("tr");["Projekt","bereit","in Arbeit","blockiert","Entwurf","PRs","rot","laufend"].forEach(x=>h.append(el("th",null,x)));t.append(h);
 s.projekte.forEach(p=>{const r=el("tr");r.append(el("td",null,p.name));
  if(p.fehler){const c=el("td",null,"Fehler: "+p.fehler);c.colSpan=7;r.append(c)}
  else[p.issues.bereit,p.issues["in-arbeit"],p.issues.blockiert,p.issues.entwurf,p.prs.offen,p.prs.rot,p.prs.laufend].forEach(x=>r.append(el("td",null,x)));
  t.append(r)});
 renderStat(s.statistik);
 renderEinstellungen(s.einstellungen);
 $("budgets").replaceChildren(...Object.entries(s.budgets).map(([k,b])=>{const d=el("div","card");
  d.append(el("b",null,k));
  if(s.schreiben&&b.max!=null){const bt=el("button",null,"Limit ändern");bt.onclick=()=>setBudget(k,b.max);d.append(" ",bt)}
  if(b.fehler)d.append(el("div","mut",b.fehler));else{
   d.append(el("div","mut","$"+b.spend+(b.max?" / $"+b.max:"")+(b.prozent!=null?" ("+b.prozent+" %)":"")));
   const bar=el("div","bar"),i=el("i",b.prozent>=90?"hi":"");i.style.width=Math.min(b.prozent||0,100)+"%";bar.append(i);d.append(bar)}
  return d}))}
const es=new EventSource("events");
es.addEventListener("state",e=>render(JSON.parse(e.data)));
es.addEventListener("log",e=>{logLines=logLines.concat(JSON.parse(e.data)).slice(-400);renderLog()});
es.onopen=()=>$("conn").textContent="";
es.onerror=()=>$("conn").textContent="(Verbindung unterbrochen)";
</script></body></html>
"""


def make_handler(dash: Dashboard, interval: float = SSE_INTERVAL):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):   # keine Zugriffszeilen ins Journal
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):   # noqa: N802 — Name vorgegeben von BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/healthz":
                self._send(200, b"ok", "text/plain")
            elif path == "/api/state":
                body = json.dumps(dash.state(), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            elif path == "/events":
                self._events()
            else:
                self._send(404, b"nicht gefunden", "text/plain; charset=utf-8")

        def _json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_POST(self):   # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/api/wake":
                self._wake()
                return
            if path == "/api/settings":
                self._settings()
                return
            if path != "/api/budget":
                self._send(404, b"nicht gefunden", "text/plain; charset=utf-8")
                return
            writer = dash.writer
            # Der Header X-Dashboard-Token löst bei fremden Seiten einen Preflight aus, den dieser
            # Dienst nie beantwortet (kein CORS): Fremde Webseiten können nichts absenden.
            if not writer.enabled:
                self._json(403, {"fehler": "Budget ändern ist nicht eingerichtet"})
                return
            if not writer.token_ok(self.headers.get("X-Dashboard-Token", "")):
                time.sleep(writer.fail_delay)
                print("dashboard: Budget ändern mit falschem Token abgelehnt", file=sys.stderr, flush=True)
                self._json(403, {"fehler": "Token falsch"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"fehler": "Ungültige Anfrage"})
                return
            try:
                value = dash.set_budget(data.get("alias"), data.get("max_budget"))
            except BudgetError as exc:
                print(f"dashboard: Budget ändern fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
                self._json(exc.status, {"fehler": str(exc)})
                return
            print(f"dashboard: Budget-Limit {data['alias']} auf {value} gesetzt", flush=True)
            self._json(200, {"ok": True, "alias": data["alias"], "max_budget": value})

        def _settings(self) -> None:
            writer = dash.settings_writer
            # Wie beim Budget: kein Login, aber der Header X-Dashboard-Token löst bei fremden
            # Seiten einen Preflight aus, den dieser Dienst nie beantwortet (kein CORS).
            if not writer.enabled:
                self._json(403, {"fehler": "Einstellungen ändern ist nicht eingerichtet"})
                return
            if not writer.token_ok(self.headers.get("X-Dashboard-Token", "")):
                time.sleep(writer.fail_delay)
                print("dashboard: Einstellungen ändern mit falschem Token abgelehnt",
                      file=sys.stderr, flush=True)
                self._json(403, {"fehler": "Token falsch"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                print("dashboard: Einstellung ändern fehlgeschlagen: Ungültige Anfrage",
                      file=sys.stderr, flush=True)
                self._json(400, {"fehler": "Ungültige Anfrage"})
                return
            try:
                value = writer.set_value(data.get("name"), data.get("value"))
            except BudgetError as exc:
                print(f"dashboard: Einstellung ändern fehlgeschlagen: {exc}",
                      file=sys.stderr, flush=True)
                self._json(exc.status, {"fehler": str(exc)})
                return
            print(f"dashboard: Einstellung {data['name']} auf {value} gesetzt", flush=True)
            self._json(200, {"ok": True, "name": data["name"], "value": value})

        def _wake(self) -> None:
            waker = dash.waker
            # Wie beim Budget: kein Login, aber der Header X-Dashboard-Token löst bei fremden
            # Seiten einen Preflight aus, den dieser Dienst nie beantwortet (kein CORS).
            if not waker.enabled:
                self._json(403, {"fehler": "Wecken ist nicht eingerichtet"})
                return
            if not waker.token_ok(self.headers.get("X-Dashboard-Token", "")):
                time.sleep(waker.fail_delay)
                print("dashboard: Wecken mit falschem Token abgelehnt", file=sys.stderr, flush=True)
                self._json(403, {"fehler": "Token falsch"})
                return
            try:
                waker.send()
            except WakeError as exc:
                print(f"dashboard: Wecken fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
                self._json(exc.status, {"fehler": str(exc)})
                return
            print("dashboard: Takt über ntfy angestoßen", flush=True)
            self._json(200, {"ok": True})

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            # Eine neue Verbindung bekommt die vorgehaltenen Log-Zeilen und den Stand sofort.
            last_seq, last_state = 0, None
            try:
                while True:
                    dash.logbuf.poll()
                    frames, last_seq, last_state = dash.frames(last_seq, last_state)
                    for frame in frames:
                        self.wfile.write(frame.encode("utf-8"))
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time.sleep(interval)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return   # Browser hat die Verbindung geschlossen

    return Handler


def serve(bind: str = BIND, port: int = PORT) -> ThreadingHTTPServer:
    stats = dashboard_stats.ReviewStats()
    dash = Dashboard(StatusCache(default_builder), LogBuffer(stats=stats), writer=BudgetWriter(),
                     waker=WakeSender())
    server = ThreadingHTTPServer((bind, port), make_handler(dash))
    server.daemon_threads = True
    notifier = dashboard_notify.Notifier()
    server.push = notifier.enabled
    if notifier.enabled:
        loop = dashboard_notify.NotifyLoop(dash, notifier)
        threading.Thread(target=loop.run, args=(NOTIFY_INTERVAL,), daemon=True).start()
    return server


def main() -> None:
    if "--notify-test" in sys.argv[1:]:
        sys.exit(dashboard_notify.send_test(dashboard_notify.notifier_for_test()))
    server = serve()
    print(f"Dashboard läuft auf {BIND}:{server.server_address[1]}, Push {'an' if server.push else 'aus'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
