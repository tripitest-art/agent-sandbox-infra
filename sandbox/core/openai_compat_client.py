"""OpenAI-kompatibler Chat-Client für den Benchmark.

Ein Aufruf, ein `dict`: Damit kann der Benchmark sowohl kostenlose Anbieter direkt als auch
LiteLLM-Aliase ansprechen, ohne pro Anbieter einen eigenen Client zu brauchen. Anders als
`litellm_client` hängt diese Funktion kein `v1/` an — `base_url` bringt die Version schon mit.

Die Funktion wirft nie und gibt weder den Schlüssel noch Fehlertexte der Gegenseite zurück
(Regel 5): Ein Ausfall oder eine unklare Antwort ist ein Ergebnis mit `ok=False`, kein
Traceback.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

# Felder, die `extra_body` niemals überschreiben darf: Sonst ginge die Anfrage an ein anderes
# Modell oder mit fremden Nachrichten hinaus als der Aufrufer verlangt hat.
_GESCHUETZT = ("model", "messages")


def _ergebnis() -> dict[str, Any]:
    """Leeres Ergebnis in der festen Form, die `chat()` immer zurückgibt."""
    return {
        "ok": False,
        "text": "",
        "fehler": None,
        "ratenbegrenzt": False,
        "dauer_s": 0.0,
        "usage": None,
        "kosten": None,
    }


def _kosten(usage: dict[str, Any]) -> float | None:
    """`usage["cost"]` als Zahl, sonst None. `bool` ist in Python eine Zahl, gilt hier aber nicht."""
    wert = usage.get("cost")
    if isinstance(wert, (int, float)) and not isinstance(wert, bool):
        return wert
    return None


def chat(
    base_url: str,
    model: str,
    prompt: str,
    api_key: str = "",
    timeout: float = 120.0,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    extra_body: dict | None = None,
) -> dict:
    """Schickt `prompt` als eine Nutzernachricht an `{base_url}/chat/completions`.

    `base_url` enthält die Versionsangabe schon (z. B. `https://host/openai/v1`); ein
    abschließender „/“ wird abgeschnitten. Wirft nie: Auch ein unbrauchbares `base_url`
    (z. B. leer) ergibt ein Ergebnis mit `ok=False`, deshalb steht das Bauen der Anfrage
    mit im try-Block.

    Rückgabe: immer die sieben Schlüssel `ok`, `text`, `fehler`, `ratenbegrenzt`, `dauer_s`,
    `usage`, `kosten`. `fehler` ist fester Klartext („leere Antwort“, „HTTP <code>“) oder der
    Klassenname der Ausnahme, nie deren Meldung. `dauer_s` misst `time.monotonic`, auch bei
    Fehlern.
    """
    start = time.monotonic()
    ergebnis = _ergebnis()
    try:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        for schluessel, wert in (extra_body or {}).items():
            if schluessel not in _GESCHUETZT:
                body[schluessel] = wert

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode(),
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout) as antwort:
            daten = json.load(antwort)

        text = ""
        if isinstance(daten, dict):
            choices = daten.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                nachricht = choices[0].get("message")
                if isinstance(nachricht, dict):
                    inhalt = nachricht.get("content")
                    if isinstance(inhalt, str):
                        text = inhalt
            usage = daten.get("usage")
            if isinstance(usage, dict):
                ergebnis["usage"] = usage
                ergebnis["kosten"] = _kosten(usage)
        if text:
            ergebnis["ok"] = True
            ergebnis["text"] = text
        else:
            ergebnis["fehler"] = "leere Antwort"
    except urllib.error.HTTPError as e:
        ergebnis["fehler"] = f"HTTP {e.code}"
        ergebnis["ratenbegrenzt"] = e.code == 429
    except Exception as e:  # Netz, Timeout, kaputtes JSON, unbrauchbare URL: nur der Typ
        ergebnis["fehler"] = type(e).__name__
    ergebnis["dauer_s"] = max(0.0, time.monotonic() - start)
    return ergebnis
