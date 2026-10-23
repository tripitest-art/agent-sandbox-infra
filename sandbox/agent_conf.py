"""Server-Konfiguration lesen: welcher Agent und ob der Context-Mode aktiv ist (#308).

Geschrieben wird `/etc/agent-sandbox.conf` allein von `install.sh`/`install-lib.sh conf_save`
(Goldene Regel 4). Diese Datei liest das Format nur, damit das Dashboard den Stand anzeigen kann.
"""
from __future__ import annotations

import os

VORGABE_PFAD = "/etc/agent-sandbox.conf"

_AGENTEN = ("qwen-code", "opencode")


def _zeilen_lesen(pfad: str) -> dict[str, str]:
    """KEY=VALUE je Zeile; die letzte Zuweisung eines Schlüssels gewinnt (wie `conf_get`)."""
    werte: dict[str, str] = {}
    with open(pfad, encoding="utf-8") as datei:
        for zeile in datei:
            zeile = zeile.strip()
            if not zeile or "=" not in zeile:
                continue
            schluessel, _, wert = zeile.partition("=")
            werte[schluessel.strip()] = wert.strip()
    return werte


def lese_agent_konfiguration() -> dict:
    """{"agent": str | None, "context_mode": bool | None}.

    Liest AGENT und CONTEXT_MODE aus einer Zeilen-Datei im Format `KEY=VALUE` (eine Zuweisung
    je Zeile, wie von `install-lib.sh conf_save` geschrieben). Pfad: Umgebungsvariable
    `AGENT_SANDBOX_CONF`, ohne diese die Vorgabe `/etc/agent-sandbox.conf`.
    Fehlende oder unlesbare Datei ergibt {"agent": None, "context_mode": None}.
    `agent` gilt nur als exakt "qwen-code" oder "opencode", jeder andere Wert (auch leer oder
    fehlend) ergibt None. `context_mode` gilt nur bei exakt "0" (-> False) oder "1" (-> True),
    jeder andere Wert (auch leer oder fehlend) ergibt None.
    Wirft nie — auch nicht bei einer kaputten oder unlesbaren Datei.
    """
    pfad = os.environ.get("AGENT_SANDBOX_CONF") or VORGABE_PFAD
    try:
        werte = _zeilen_lesen(pfad)
    except (OSError, UnicodeDecodeError):
        # Fehlende Datei (noch nie installiert), Rechte (Modus 600, root) oder Binärmüll:
        # die Anzeige bleibt leer, statt den Takt oder das Dashboard abzubrechen.
        werte = {}

    agent = werte.get("AGENT")
    context_mode = werte.get("CONTEXT_MODE")
    return {
        "agent": agent if agent in _AGENTEN else None,
        "context_mode": {"1": True, "0": False}.get(context_mode),
    }
