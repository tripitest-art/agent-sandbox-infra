#!/usr/bin/env python3
"""Workflow-Status des Agenten als JSON an einen Home-Assistant-Webhook schicken (Teil 1 von 2).

Sammelt den Stand des Agenten-Workflows je Projekt aus der Projektliste:
offene Issues mit Label `worker-task` nach Zustandslabel (`bereit`, `in-arbeit`,
`blockiert`, `entwurf`), offene PRs (offen/rot/laufend) sowie die PRs mit Label
`braucht-stephan`, an denen Stephan arbeiten muss. Dazu die Tagesbudgets der LiteLLM-Keys
(`spend`, `max_budget`, Prozent). Alles geht als JSON an `HA_STATUS_URL`.

Aufruf: `python sandbox/ha_status.py [--print]`. Mit `--print` wird der Status nur auf
stdout ausgegeben; sonst per POST an den Webhook aus `/etc/ha-status.env` (Modus 600).
Weder die Webhook-URL noch die Keys erscheinen je in einer Ausgabe (Regel 5: das Repo ist
öffentlich). Timer, `install.sh` und die HA-Sensoren folgen in Teil 2.

Ablauf je Projekt: `gh issue list`/`gh pr list` (nur lesend), Fehler werden je Projekt
abgefangen und als Name der Exception-Klasse gemeldet — nie der Meldungstext, er könnte
Zugangsdaten enthalten. Nur Standardbibliothek und `projects.py` liegen daneben.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # projects.py und dashboard.py liegen daneben
import projects  # noqa: E402
from dashboard import (  # noqa: E402
    BUDGET_KEYS,
    DEFAULT_LITELLM,
    RED_CONCLUSIONS,
    STATES,
    WAITING_LABEL,
    _default_key_info,
    _gh_fetch,
    _labels,
    _pr_state,
    build_status,
    summarize_budget,
    summarize_project,
)


def post_status(url: str, payload: dict, opener=urllib.request.urlopen, timeout: float = 10) -> bool:
    """payload als JSON-POST an url. True bei Status 2xx, sonst False. Gibt die URL nie aus."""
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = opener(request, timeout=timeout)
        try:
            status = getattr(resp, "status", None)
            return status is not None and 200 <= status < 300
        finally:
            resp.close()
    except Exception:   # noqa: BLE001 — Netzfehler und fremde Antworten zählen nur als „nicht gesendet“
        return False


def main(argv: list[str] | None = None, env: dict | None = None, *, fetch=None, key_info=None,
         opener=None, now: datetime | None = None) -> int:
    """`python sandbox/ha_status.py [--print]`. Rückgabe 0 (gesendet/gedruckt), 1 (Senden
    fehlgeschlagen) oder 2 (HA_STATUS_URL fehlt). URL und Keys werden nie ausgegeben."""
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    fetch = fetch or _gh_fetch
    key_info = key_info or (lambda key: _default_key_info(key, env.get("LITELLM") or DEFAULT_LITELLM))

    url = env.get("HA_STATUS_URL")
    if "--print" not in argv and not url:
        print("HA_STATUS_URL fehlt (/etc/ha-status.env)", file=sys.stderr)
        return 2

    items = projects.load_projects(env=env)
    budgets: dict[str, dict | None] = {}
    for var, alias in BUDGET_KEYS:
        key = env.get(var)
        if key:
            budgets[alias] = key_info(key)

    status = build_status(items, fetch, budgets, now)

    if "--print" in argv:
        print(json.dumps(status, ensure_ascii=False, indent=1))
        return 0
    if not post_status(url, status, opener=opener or urllib.request.urlopen):
        print("ha_status: Senden fehlgeschlagen", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
