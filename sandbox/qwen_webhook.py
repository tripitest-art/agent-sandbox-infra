#!/usr/bin/env python3
"""Schickt neue Zeilen aus /root/worker.log per Home-Assistant-Webhook (ersetzt qwen-mqtt).

Warum nicht mehr MQTT: Mit dem MQTT-Login konnte die Sandbox am Broker alles lesen und
schreiben (Zendure, Frigate, Zigbee2MQTT); die ACL der Mosquitto-App greift nicht, weil das
Auth-Plugin go-auth vorher erlaubt. Ein Webhook kann nur einen einzigen HA-Auslöser bedienen.

Ablauf: wie `tail -n 0 -F` ab dem aktuellen Dateiende lesen, neue Zeilen INTERVAL Sekunden
sammeln und als JSON {"lines": "..."} an HA_WEBHOOK_URL schicken. Scheitert das Senden,
bleiben die Zeilen (höchstens MAX_CHARS) für den nächsten Versuch liegen. Wird das Log
abgeschnitten oder ersetzt, liest das Skript die neue Datei von vorn.

HA_WEBHOOK_URL in /etc/qwen-webhook.env (nie im Repo) ist die Webhook-ID oder die volle URL
http://<HA>:8123/api/webhook/<ID>; bei einer reinen ID baut das Skript die URL mit HA_URL.
Weder ID noch URL erscheinen je in Ausgaben.
"""
import json
import os
import re
import sys
import time
import urllib.request

LOG = os.environ.get("WORKER_LOG", "/root/worker.log")
HA_URL = os.environ.get("HA_URL", "http://192.168.0.148:8123")   # für eine reine Webhook-ID
INTERVAL = float(os.environ.get("WEBHOOK_INTERVAL", "2"))
MAX_CHARS = 2000   # der HA-Sensor hält ohnehin nur die letzten 2000 Zeichen
# Verzeichnis mit dem Slot-Status, den worker_tick.py schreibt (/run/worker-slots/slot-<n>.json).
SLOT_DIR: str = os.environ.get("SLOT_DIR", "/run/worker-slots")


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # dashboard.py liegt daneben
from dashboard import (  # noqa: E402
    Follower,
    pid_is_active,
    read_slots,
    strip_ansi,
)


def payload(lines: list[str], slots: dict | None = None) -> bytes:
    text = strip_ansi("\n".join(lines))[-MAX_CHARS:]
    data: dict = {"lines": text}
    if slots is not None:
        data["slots"] = slots
    return json.dumps(data, ensure_ascii=False).encode()


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,}$")


def webhook_url(value: str, ha_url: str = HA_URL) -> str | None:
    """URL aus HA_WEBHOOK_URL: eine volle http(s)-URL oder nur die Webhook-ID.

    Die ID ist das Geheimnis. Diese Funktion und ihre Aufrufer geben den Wert nie aus
    (am 01.10. stand eine ID durch eine Fehlermeldung von urllib im Journal).
    """
    value = (value or "").strip()
    if value.startswith(("http://", "https://")) and "/api/webhook/" in value:
        return value
    if _ID_RE.match(value):
        return f"{ha_url.rstrip('/')}/api/webhook/{value}"
    return None


def send(url: str, lines: list[str], slots: dict | None = None,
         opener=urllib.request.urlopen) -> bool:
    try:
        req = urllib.request.Request(url, data=payload(lines, slots), method="POST",
                                     headers={"Content-Type": "application/json"})
        with opener(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception as e:  # nur der Fehlertyp, nie die URL (enthält die ID)
        print(f"Senden fehlgeschlagen: {type(e).__name__}", file=sys.stderr, flush=True)
        return False


def _cap(pending: list[str]) -> list[str]:
    """Nicht senden können: Rest behalten, aber nie mehr als MAX_CHARS (neueste Zeilen zuerst)."""
    kept, size = [], 0
    for ln in reversed(pending):
        size += len(ln) + 1
        if size > MAX_CHARS:
            break
        kept.append(ln)
    return list(reversed(kept))


def step(follower: Follower, pending: list[str], url: str, last_slots: dict | None = None,
         slot_dir: str = SLOT_DIR, sender=send, is_pid_alive=pid_is_active):
    """Ein Takt: neue Zeilen und Slot-Status holen und senden.

    Ohne last_slots unverändert: nur Log-Zeilen, Rückgabe ist die Liste der offenen Zeilen.
    Mit last_slots (dict): zusätzlich die Slots senden, sobald neue Zeilen oder ein geänderter
    Slot-Status vorliegen; Rückgabe ist (offene Zeilen, aktueller Slot-Stand). Der Takt merkt
    sich den Stand und schickt erst bei Änderung — sonst ginge alle 2 s ein Webhook raus.
    """
    pending = pending + follower.poll()
    if last_slots is None:
        if not pending:
            return []
        return [] if sender(url, pending) else _cap(pending)
    slots = read_slots(slot_dir, is_pid_alive)
    if not pending and slots == last_slots:
        return pending, last_slots
    if sender(url, pending, slots):
        return [], slots
    return _cap(pending), slots


def main() -> None:
    url = webhook_url(os.environ.get("HA_WEBHOOK_URL", ""))
    if url is None:
        sys.exit("HA_WEBHOOK_URL fehlt oder ist ungültig (Wert wird nicht angezeigt). "
                 "Erwartet: Webhook-ID oder http://<HA>:8123/api/webhook/<ID>")
    follower, pending, last_slots = Follower(LOG), [], {}
    while True:
        pending, last_slots = step(follower, pending, url, last_slots)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
