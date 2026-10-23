"""Einstellungsdatei der Takte: eine feste Liste unkritischer Werte (#164).

Die Datei (Vorgabe `/etc/agent-settings.json`, Pfad über `AGENT_SETTINGS`) gewinnt vor der
Umgebung, die Umgebung vor der Vorgabe im Code. Gelesen wird bei jedem Aufruf neu; jeder Taktlauf
ist ein eigener Prozess, eine Änderung gilt also ab dem nächsten Takt, ohne Neustart. Ungültige
Werte werden still verworfen, ein Lauf bricht deswegen nie ab.

Nicht in dieser Liste und nicht ergänzbar sind die Werte, die die Aufsicht schützen:
`REVIEW_MODEL` und `REVIEWER_NAME` (ein falsches Paar sperrt jeden Review), `auto_merge` und
`manual_paths` (Regel 6), die Timer-Intervalle (`systemctl`, Regel 4) sowie alles mit Schlüsseln,
Tokens oder Pfaden.
"""
import json
import os
import tempfile
from pathlib import Path

VORGABE_PFAD = "/etc/agent-settings.json"


class SettingError(ValueError):
    """Name oder Wert passt nicht zur festen Liste der Einstellungen."""


# "umgebung": Die gleichnamige Umgebungsvariable gilt, wenn die Datei nichts Brauchbares hat.
SPECS: dict[str, dict] = {
    "REVIEW_MAX_TOKENS": {
        "text": "Obergrenze der Reviewer-Antwort in Tokens",
        "vorgabe": 16000, "min": 1000, "max": 100000, "umgebung": True,
    },
    "REVIEW_LLM_TIMEOUT": {
        "text": "Zeitlimit je Modellaufruf des Reviewers in Sekunden",
        "vorgabe": 420, "min": 60, "max": 900, "umgebung": True,
    },
    "MAX_ROUNDS": {
        "text": "Reviews „Änderungen nötig“ je PR, danach liegt er bei Stephan",
        "vorgabe": 2, "min": 1, "max": 5,
    },
    "MAX_TRIES": {
        "text": "Versuche je Auftrag, danach liegt er bei Stephan",
        "vorgabe": 2, "min": 1, "max": 5,
    },
    "MISSING_CHECK_GRACE_S": {
        "text": "Kulanzzeit für einen noch nicht gestarteten Pflicht-Check in Sekunden",
        "vorgabe": 600, "min": 60, "max": 3600,
    },
    "REVIEW_MODEL_BIG": {
        "text": "Modell-Alias des Reviewers für große Diffs",
        "vorgabe": None, "erlaubt": ["coder-glm", "longctx-lite", "longctx"], "umgebung": True,
    },
    "LITELLM_MODEL": {
        "text": "Primäres Worker-Modell (LiteLLM-Alias)",
        "vorgabe": "coder",
        "erlaubt": [
            "coder", "coder-pro", "coder-fast", "coder-or",
            "coder-free", "coder-glm", "coder-glm-free", "coder-qwen",
            "coder-nvidia", "coder-nemotron-free", "coder-minimax-free",
            "coder-beam-free",
        ],
        "umgebung": True,
    },
}


def _pfad() -> Path:
    return Path(os.environ.get("AGENT_SETTINGS") or VORGABE_PFAD)


def validate(name: object, wert: object):
    """Gibt `wert` zurück, wenn Name und Wert in der festen Liste stehen, sonst SettingError.

    Ganzzahlen müssen echte `int` sein (kein `bool`, `str` oder `float`), Text muss genau einem
    erlaubten Alias entsprechen. Die Meldung nennt den Wert nicht.
    """
    if not isinstance(name, str) or name not in SPECS:
        raise SettingError("Unbekannte Einstellung.")
    spec = SPECS[name]
    if "erlaubt" in spec:
        if not isinstance(wert, str) or wert not in spec["erlaubt"]:
            raise SettingError(f"{name}: erlaubt sind {', '.join(spec['erlaubt'])}.")
        return wert
    if isinstance(wert, bool) or not isinstance(wert, int):
        raise SettingError(f"{name}: Wert muss eine ganze Zahl sein.")
    if not spec["min"] <= wert <= spec["max"]:
        raise SettingError(f"{name}: Wert muss zwischen {spec['min']} und {spec['max']} liegen.")
    return wert


def load() -> dict:
    """Die gültigen Werte aus der Datei; fehlende oder kaputte Datei und fremde Werte ergeben nichts."""
    try:
        roh = json.loads(_pfad().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(roh, dict):
        return {}
    werte = {}
    for name, wert in roh.items():
        try:
            werte[name] = validate(name, wert)
        except SettingError:
            continue
    return werte


def _aus_umgebung(name: str):
    """Brauchbarer Wert der Umgebungsvariable, sofern dieser Eintrag sie lesen darf, sonst None."""
    spec = SPECS[name]
    if not spec.get("umgebung"):
        return None
    roh = os.environ.get(name)
    if not roh:
        return None
    if "erlaubt" in spec:   # REVIEW_MODEL_BIG gilt unverändert, wenn nicht leer
        return roh
    try:
        return validate(name, int(roh))
    except (ValueError, SettingError):
        return None


def value(name: str, default):
    """Datei vor Umgebung vor `default`; eine ungültige Quelle fällt auf die nächste zurück."""
    if name not in SPECS:
        return default
    werte = load()
    if name in werte:
        return werte[name]
    aus_umgebung = _aus_umgebung(name)
    return default if aus_umgebung is None else aus_umgebung


def aktuelle_werte() -> dict:
    """{name: {"wert": …, "quelle": "datei" | "umgebung" | "vorgabe"}} für alle Einträge von SPECS."""
    werte = load()
    ergebnis = {}
    for name, spec in SPECS.items():
        if name in werte:
            ergebnis[name] = {"wert": werte[name], "quelle": "datei"}
            continue
        aus_umgebung = _aus_umgebung(name)
        if aus_umgebung is not None:
            ergebnis[name] = {"wert": aus_umgebung, "quelle": "umgebung"}
        else:
            ergebnis[name] = {"wert": spec["vorgabe"], "quelle": "vorgabe"}
    return ergebnis


def save(werte: dict) -> None:
    """Prüft alle Werte, ergänzt die gültigen Dateiwerte und schreibt die Datei atomar (0o644)."""
    geprueft = {name: validate(name, wert) for name, wert in werte.items()}
    ziel = _pfad()
    zusammen = load()
    zusammen.update(geprueft)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{ziel.name}.", suffix=".tmp", dir=ziel.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as datei:
            json.dump(zusammen, datei, ensure_ascii=False, indent=2, sort_keys=True)
            datei.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, ziel)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
