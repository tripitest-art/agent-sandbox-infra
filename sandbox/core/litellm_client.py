"""LiteLLM-Proxy als eigenständiges Modul: Modell-Probe und Key-Auskunft.

Beide Funktionen werfen nicht: Ein Ausfall des Proxy ist ein Betriebszustand, den der Takt
meldet, statt mit einem Traceback zu enden. Der Key steht nur im Header und taucht in keiner
Fehlermeldung auf (Regel 5).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def _url(base_url: str, pfad: str) -> str:
    """Basis-Adresse und Pfad verbinden, egal ob die Basis mit „/“ endet."""
    return f"{base_url.rstrip('/')}/{pfad.lstrip('/')}"


def probe_model(
    base_url: str,
    model: str,
    api_key: str = "",
    timeout: float = 5.0,
) -> tuple[bool, str | None]:
    """Prüft mit einem Mini-Prompt, ob `model` über den Proxy antwortet.

    Gibt (True, None) zurück, wenn die Antwort `choices` enthält, sonst (False, Grund). Der
    Grund ist ein fester Klartext („HTTP 429“, „TimeoutError“, „leere Antwort“), nie der
    Antworttext des Proxy. Ohne Streaming: Dann schickt LiteLLM erst mit der fertigen Antwort
    Daten, und `timeout` greift auch, wenn der Anbieter die Verbindung nur offen hält.
    `disable_fallbacks`, damit die Probe das Modell selbst prüft und nicht still dessen
    Ausweichmodell.
    """
    body = json.dumps({
        "model": model,
        "max_tokens": 16,
        "stream": False,
        "disable_fallbacks": True,
        "messages": [{"role": "user", "content": "Antworte nur mit: ok"}],
    }).encode()
    req = urllib.request.Request(
        _url(base_url, "v1/chat/completions"),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # Timeout, Verbindung, kaputtes JSON: nur der Typ
        return False, type(e).__name__
    if isinstance(data, dict) and data.get("choices"):
        return True, None
    return False, "leere Antwort"


def get_key_info(
    base_url: str,
    api_key: str,
    timeout: float = 10.0,
) -> dict[str, Any] | None:
    """Verbrauch und Limit des Keys aus GET {base_url}/key/info.

    None bei jedem Fehler (HTTP, Netz, Timeout, kein JSON, Antwort kein Objekt): Eine unklare
    Lage darf die Pipeline nicht stilllegen. Der Key steht nur im Header.
    """
    req = urllib.request.Request(
        _url(base_url, "key/info"),
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return None
    return data if isinstance(data, dict) else None
