#!/usr/bin/env python3
"""Review- und Merge-Statistik für das Dashboard, gezählt aus `/root/audit.jsonl`.

Primärquelle ist das strukturierte Audit-Log (JSON Lines), das seit #222 von
`worker_tick.py` und `review_tick.py` befüllt wird. Als Fallback für historische
Daten (vor Einführung des Audit-Logs) wird weiterhin `/root/worker.log` per Regex
ausgewertet.

Gezählt in zwei Fenstern, 24 Stunden und 7 Tage (Ortszeit des Servers):

- `freigegeben` / `aenderungen`: gepostete Reviews (aus `review_posted` mit `urteil`)
- `gemergt` / `merge_fehler`: `gh pr merge` (aus `pr_merged` / Log-Fallback)
- `an_stephan`: PR an Stephan übergeben (aus `stephan_alarm` / `to_stephan`)
- `worker_fehler`: Worker-Takt Alarm (aus `stephan_alarm` mit skey beginnend mit "issue"/"pr"/"text")
- `quote`: Anteil `freigegeben` an allen Reviews in Prozent, sonst None
- `modelle`: dieselben Zahlen für Reviews je Reviewer-Alias (7 Tage, aus `review_posted`)

Nur Standardbibliothek. Das Modul liest nur; es schreibt nichts.
"""
from __future__ import annotations

import json
import os
import re
from collections import deque
from datetime import datetime, timedelta, timezone

PRELOAD_BYTES = 20 * 1024 * 1024   # so weit zurück wird das Log beim Start gelesen
MAX_EVENTS = 20000                 # Obergrenze, falls das Log außergewöhnlich voll ist
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
REVIEW_KINDS = ("freigegeben", "aenderungen")
# Kategorien für das Dashboard (inkl. worker_fehler für Log-Fallback)
KINDS = ("freigegeben", "aenderungen", "gemergt", "merge_fehler", "an_stephan", "worker_fehler")

# Audit-Event-Typen, die wir auswerten
AUDIT_REVIEW_POSTED = "review_posted"
AUDIT_PR_MERGED = "pr_merged"
AUDIT_STEPHAN_ALARM = "stephan_alarm"
AUDIT_TO_STEPHAN = "to_stephan"
AUDIT_RUN_FINISHED = "run_finished"
AUDIT_PR_OPENED = "pr_opened"
AUDIT_AGENT_RUN_FINISHED = "agent_run_finished"

# Regex für Log-Fallback (wie bisher)
_TS = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
_REVIEW = re.compile(rf"^{_TS} \[review(?::([^\]]+))?\] PR #\d+: (.*)$")
_WORKER = re.compile(rf"^{_TS}(?: \[w[^\]]*\])? ⚠ Stephan: ")
_REVIEW_RULES = (
    ("freigegeben", re.compile(r"^Freigegeben, \d+ Muss / \d+ Kann, als Kommentar-Review gepostet(?:, Reviewer (\S+))?")),
    ("aenderungen", re.compile(r"^Änderungen nötig, \d+ Muss / \d+ Kann, als Kommentar-Review gepostet(?:, Reviewer (\S+))?")),
    ("gemergt", re.compile(r"^erfolgreich gemergt")),
    ("merge_fehler", re.compile(r"^Merge fehlgeschlagen")),
    ("an_stephan", re.compile(r"^⚠ Stephan: ")),
)
_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _parse_iso(text: str) -> datetime | None:
    """ISO-8601-Text als datetime mit Zeitzone; None bei Fehler."""
    if not isinstance(text, str):
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_log_ts(text: str) -> datetime:
    """Log-Zeitstempel (lokal, ohne Zeitzone) als naive datetime.
    
    Wirft ValueError bei ungültigem Format (wie das alte _parse)."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


def classify_audit(event: dict) -> tuple[datetime, str, str, str] | None:
    """Wandelt ein Audit-Event in (Zeit, Art, Projekt, Reviewer) um.
    
    Returns None, wenn das Event nicht für das Dashboard relevant ist.
    """
    ts = _parse_iso(event.get("ts", ""))
    if ts is None:
        return None
    # Zeitstempel in lokale Zeit konvertieren für Konsistenz mit Log-Fallback
    ts_local = ts.astimezone().replace(tzinfo=None)
    
    event_type = event.get("event")
    
    # review_posted: hat urteil (Freigegeben/Änderungen nötig), projekt, model/reviewer
    if event_type == AUDIT_REVIEW_POSTED:
        urteil = event.get("urteil", "")
        projekt = event.get("projekt", "-")
        reviewer = event.get("model", "unbekannt")
        if "Freigegeben" in urteil:
            return ts_local, "freigegeben", projekt, reviewer
        elif "Änderungen nötig" in urteil:
            return ts_local, "aenderungen", projekt, reviewer
        return None
    
    # pr_merged
    if event_type == AUDIT_PR_MERGED:
        return ts_local, "gemergt", event.get("repo", "-"), "-"
    
    # stephan_alarm: pr oder worker_fehler
    if event_type == AUDIT_STEPHAN_ALARM:
        # Prüfen ob es ein Worker-Fehler ist (skey wie "issue123", "pr456@", "text789@")
        # oder ein PR-bezogener Alarm
        skey = event.get("skey", "")
        if isinstance(skey, str) and (skey.startswith("issue") or skey.startswith("pr") or skey.startswith("text")):
            return ts_local, "worker_fehler", "-", "-"
        # Sonst PR-bezogen (to_stephan etc.)
        return ts_local, "an_stephan", event.get("projekt", "-"), "-"
    
    # to_stephan (Legacy, wird durch stephan_alarm abgelöst)
    if event_type == AUDIT_TO_STEPHAN:
        return ts_local, "an_stephan", event.get("repo", "-"), "-"
    
    return None


def classify_log(line: str) -> tuple[datetime, str, str, str] | None:
    """(Zeit, Art, Projekt, Reviewer) einer Logzeile oder None (Fallback für Historie)."""
    line = _ANSI.sub("", line.rstrip("\r\n"))
    m = _REVIEW.match(line)
    if m:
        for kind, rule in _REVIEW_RULES:
            hit = rule.match(m.group(3))
            if hit:
                reviewer = (hit.group(1) or "unbekannt") if kind in REVIEW_KINDS else "-"
                return _parse_log_ts(m.group(1)), kind, m.group(2) or "-", reviewer
        return None
    m = _WORKER.match(line)
    if m:
        return _parse_log_ts(m.group(1)), "worker_fehler", "-", "-"
    return None


class ReviewStats:
    """Sammelt die gezählten Ereignisse und fasst sie je Fenster zusammen."""

    def __init__(self) -> None:
        self.events: deque[tuple[datetime, str, str, str]] = deque(maxlen=MAX_EVENTS)

    def feed_audit(self, event: dict) -> None:
        """Füttert die Statistik mit einem Audit-Event."""
        hit = classify_audit(event)
        if hit:
            self.events.append(hit)

    def feed_log(self, line: str) -> None:
        """Füttert die Statistik mit einer Logzeile (Fallback)."""
        try:
            hit = classify_log(line)
        except ValueError:
            return
        if hit:
            self.events.append(hit)

    def preload_audit(self, path: str, max_bytes: int = PRELOAD_BYTES) -> None:
        """Liest die letzten `max_bytes` aus dem Audit-Log."""
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                start = max(0, size - max_bytes)
                fh.seek(start)
                text = fh.read(size - start).decode("utf-8", errors="replace")
        except OSError:
            return
        lines = text.split("\n")
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                event = json.loads(ln)
                if isinstance(event, dict):
                    self.feed_audit(event)
            except json.JSONDecodeError:
                continue

    def preload_log(self, path: str, upto: int, max_bytes: int = PRELOAD_BYTES) -> None:
        """Liest das Log bis zur Stelle `upto` (Fallback für Historie)."""
        try:
            with open(path, "rb") as fh:
                start = max(0, upto - max_bytes)
                fh.seek(start)
                text = fh.read(upto - start).decode("utf-8", errors="replace")
        except OSError:
            return
        lines = text.split("\n")
        for ln in lines[1:] if start > 0 else lines:
            self.feed_log(ln)

    # --- Rückwärtskompatibilität für bestehende Tests und dashboard.py ---
    feed = feed_log
    preload = preload_log

    @staticmethod
    def _counts(events) -> dict:
        counts: dict = {k: 0 for k in KINDS}
        for _, kind, _, _ in events:
            counts[kind] += 1
        reviews = counts["freigegeben"] + counts["aenderungen"]
        counts["quote"] = round(counts["freigegeben"] / reviews * 100) if reviews else None
        return counts

    def snapshot(self, now: datetime) -> dict:
        """{"24h": Zahlen, "7d": Zahlen, "projekte": {Name: …}, "modelle": {Alias: …}} (Letztere je 7 Tage)."""
        week = [e for e in self.events if now - WINDOWS["7d"] <= e[0] <= now]
        out: dict = {}
        for name, span in WINDOWS.items():
            out[name] = self._counts(e for e in week if e[0] >= now - span)
        by_project: dict[str, list] = {}
        for e in week:
            if e[2] != "-":
                by_project.setdefault(e[2], []).append(e)
        out["projekte"] = {name: self._counts(evs) for name, evs in sorted(by_project.items())}
        by_model: dict[str, list] = {}
        for e in week:
            if e[3] != "-":
                by_model.setdefault(e[3], []).append(e)
        out["modelle"] = {name: self._counts(evs) for name, evs in sorted(by_model.items())}
        return out


def build_stats(audit_log: str = "/root/audit.jsonl", worker_log: str = "/root/worker.log", now: datetime | None = None) -> dict:
    """Baut die vollständige Statistik aus Audit-Log (primär) und Worker-Log (Fallback).

    `now` ist für Tests; Default ist aktuelle Ortszeit.
    """
    if now is None:
        now = datetime.now()
    stats = ReviewStats()
    # 1. Primär: Audit-Log (strukturiert, vollständig seit #222)
    stats.preload_audit(audit_log)
    # 2. Fallback: Worker-Log für historische Daten vor Audit-Log-Einführung
    #    Wir lesen die letzten PRELOAD_BYTES, um Lücken zu füllen.
    try:
        with open(worker_log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            upto = fh.tell()
    except OSError:
        upto = 0
    if upto:
        stats.preload_log(worker_log, upto)
    return stats.snapshot(now)


# Modulare Rückwärtskompatibilität
classify = classify_log