#!/usr/bin/env python3
"""Schickt neue Zeilen aus /root/worker.log per Home-Assistant-Webhook (ersetzt qwen-mqtt).

Warum nicht mehr MQTT: Mit dem MQTT-Login konnte die Sandbox am Broker alles lesen und
schreiben (Zendure, Frigate, Zigbee2MQTT); die ACL der Mosquitto-App greift nicht, weil das
Auth-Plugin go-auth vorher erlaubt. Ein Webhook kann nur einen einzigen HA-Auslöser bedienen.

Ablauf: wie `tail -n 0 -F` ab dem aktuellen Dateiende lesen, neue Zeilen INTERVAL Sekunden
sammeln und als JSON {"lines": "..."} an HA_WEBHOOK_URL schicken. Scheitert das Senden,
bleiben die Zeilen (höchstens MAX_CHARS) für den nächsten Versuch liegen. Wird das Log
abgeschnitten oder ersetzt, liest das Skript die neue Datei von vorn.

Die URL (enthält die Webhook-ID) kommt aus /etc/qwen-webhook.env, nie aus dem Repo.
"""
import json
import os
import time
import urllib.request

LOG = os.environ.get("WORKER_LOG", "/root/worker.log")
INTERVAL = float(os.environ.get("WEBHOOK_INTERVAL", "2"))
MAX_CHARS = 2000   # der HA-Sensor hält ohnehin nur die letzten 2000 Zeichen


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


def payload(lines: list[str]) -> bytes:
    text = "\n".join(lines)[-MAX_CHARS:]
    return json.dumps({"lines": text}, ensure_ascii=False).encode()


def send(url: str, lines: list[str], opener=urllib.request.urlopen) -> bool:
    req = urllib.request.Request(url, data=payload(lines), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with opener(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def step(follower: Follower, pending: list[str], url: str, sender=send) -> list[str]:
    """Ein Takt: neue Zeilen holen, senden; liefert, was noch aussteht."""
    pending = pending + follower.poll()
    if not pending:
        return []
    if sender(url, pending):
        return []
    # Nicht senden können: Rest behalten, aber nie mehr als MAX_CHARS
    kept, size = [], 0
    for ln in reversed(pending):
        size += len(ln) + 1
        if size > MAX_CHARS:
            break
        kept.append(ln)
    return list(reversed(kept))


def main() -> None:
    url = os.environ["HA_WEBHOOK_URL"]
    follower, pending = Follower(LOG), []
    while True:
        pending = step(follower, pending, url)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
