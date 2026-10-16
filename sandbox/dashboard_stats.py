#!/usr/bin/env python3
"""Review- und Merge-Statistik für das Dashboard, gezählt aus `/root/worker.log`.

Es gibt keine eigene Datenquelle: Der Review-Takt (`review_tick.py`) und der Worker-Takt
(`worker_tick.py`) schreiben ihre Ergebnisse als Textzeilen ins Log, die Zeilen werden hier
erkannt und gezählt. Die Zahlen sind deshalb nur so genau wie das Log (kein Verlauf vor dem
Beginn der Logdatei). Das Modell steht erst seit `review_tick.py` „…gepostet, Reviewer <Alias>“ in der
Zeile; ältere Zeilen zählen unter „unbekannt“, rote Checks ohne Modellaufruf unter „ohne-Modell“.

Gezählt in zwei Fenstern, 24 Stunden und 7 Tage (Ortszeit des Servers, wie der Zeitstempel im Log):

- `freigegeben` / `aenderungen`: gepostete Reviews „Freigegeben“ / „Änderungen nötig“,
- `gemergt` / `merge_fehler`: `gh pr merge` hat geklappt / ist gescheitert,
- `an_stephan`: der Review-Takt hat einen PR an Stephan übergeben (`PR #N: ⚠ Stephan: …`),
- `worker_fehler`: der Worker-Takt hat `⚠ Stephan: <Auftrag>: …` geschrieben,
- `quote`: Anteil `freigegeben` an allen Reviews in Prozent, sonst None,
- `modelle`: dieselben Zahlen für Reviews je Reviewer-Alias (7 Tage).

Nur Standardbibliothek. Das Modul liest nur; es schreibt nichts.
"""
from __future__ import annotations

import os
import re
from collections import deque
from datetime import datetime, timedelta

PRELOAD_BYTES = 20 * 1024 * 1024   # so weit zurück wird das Log beim Start gelesen
MAX_EVENTS = 20000                 # Obergrenze, falls das Log außergewöhnlich voll ist
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
REVIEW_KINDS = ("freigegeben", "aenderungen")
KINDS = ("freigegeben", "aenderungen", "gemergt", "merge_fehler", "an_stephan", "worker_fehler")

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


def classify(line: str) -> tuple[datetime, str, str, str] | None:
    """(Zeit, Art, Projekt, Reviewer) einer Logzeile oder None, wenn sie nicht gezählt wird.
    Reviewer ist nur bei Reviews gesetzt (sonst „-“), ohne Angabe in der Zeile „unbekannt“."""
    line = _ANSI.sub("", line.rstrip("\r\n"))
    m = _REVIEW.match(line)
    if m:
        for kind, rule in _REVIEW_RULES:
            hit = rule.match(m.group(3))
            if hit:
                reviewer = (hit.group(1) or "unbekannt") if kind in REVIEW_KINDS else "-"
                return _parse(m.group(1)), kind, m.group(2) or "-", reviewer
        return None
    m = _WORKER.match(line)
    if m:
        return _parse(m.group(1)), "worker_fehler", "-", "-"
    return None


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


class ReviewStats:
    """Sammelt die gezählten Ereignisse und fasst sie je Fenster zusammen."""

    def __init__(self) -> None:
        self.events: deque[tuple[datetime, str, str, str]] = deque(maxlen=MAX_EVENTS)

    def feed(self, line: str) -> None:
        try:
            hit = classify(line)
        except ValueError:   # kaputter Zeitstempel: Zeile ignorieren
            return
        if hit:
            self.events.append(hit)

    def preload(self, path: str, upto: int, max_bytes: int = PRELOAD_BYTES) -> None:
        """Liest das Log bis zur Stelle `upto` (dort setzt der Follower des Dashboards auf, so wird nichts doppelt gezählt)."""
        try:
            with open(path, "rb") as fh:
                start = max(0, upto - max_bytes)
                fh.seek(start)
                text = fh.read(upto - start).decode("utf-8", errors="replace")
        except OSError:
            return
        lines = text.split("\n")
        for ln in lines[1:] if start > 0 else lines:   # erste Zeile ist dann angeschnitten
            self.feed(ln)

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
