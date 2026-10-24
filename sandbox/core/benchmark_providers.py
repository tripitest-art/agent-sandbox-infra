"""Providerliste für den Benchmark: laden, prüfen, Schlüssel aus der Umgebung auflösen (#334).

Teil 2 von 5 des Benchmark-Moduls. Das Modul kennt weder Netzwerk noch Chat-Client: Es liest
eine JSON-Datei, prüft jeden Eintrag vollständig und löst erst danach die Namen der
Umgebungsvariablen in Schlüssel auf. Ein fehlender Schlüssel überspringt genau seinen Eintrag,
ein fehlerhafter Eintrag bricht die ganze Liste ab — geprüft wird also alles oder nichts.

Regel 5: Zugangsdaten stehen nie in der Datei, nur der Name der Umgebungsvariable. Deshalb
weist `ProviderFehler` das Feld `api_key` zurück, und die Meldungen nennen nur Eintragsnummer
und Feldnamen, niemals Werte.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

DEFAULT_PFAD = "/etc/agent-benchmark-providers.json"

_ART_ALIAS = "litellm-alias"
_ART_OPENAI = "openai-kompatibel"
_LITELLM_GRUND = "LITELLM oder LITELLM_BENCHMARK_KEY nicht gesetzt"
_NAME = re.compile(r"[A-Za-z0-9._:-]+")
_UMGEBUNG = re.compile(r"[A-Z][A-Z0-9_]*")
_SCHEMATA = ("http://", "https://")


class ProviderFehler(ValueError):
    """Die Providerliste ist unbrauchbar. Meldungen nennen Eintragsnummer und Feld, nie Werte."""


@dataclass(frozen=True)
class Provider:
    name: str
    art: str
    base_url: str
    modell: str
    api_key: str = field(default="", repr=False, compare=False)
    extra_body: dict = field(default_factory=dict, compare=False)
    rolle: str = ""


def _feld(eintrag: dict, feldname: str, nr: int) -> object:
    if feldname not in eintrag:
        raise ProviderFehler(f"Eintrag {nr}: Feld '{feldname}' fehlt")
    return eintrag[feldname]


def _pflicht_text(eintrag: dict, feldname: str, nr: int) -> str:
    wert = _feld(eintrag, feldname, nr)
    if not isinstance(wert, str) or not wert:
        raise ProviderFehler(f"Eintrag {nr}: Feld '{feldname}' muss ein nicht leerer Text sein")
    return wert


def _base_url(eintrag: dict, nr: int) -> str:
    """Prüft Schema und Abwesenheit von Zugangsdaten; Zugangsdaten stecken im Autoritätsteil."""
    wert = _feld(eintrag, "base_url", nr)
    if isinstance(wert, str):
        for schema in _SCHEMATA:
            if wert.startswith(schema):
                autoritaet = wert[len(schema):].split("/", 1)[0]
                if autoritaet and "@" not in autoritaet:
                    return wert.rstrip("/")
                break
    raise ProviderFehler(
        f"Eintrag {nr}: Feld 'base_url' muss mit http:// oder https:// beginnen, einen Host "
        "nennen und darf keine Zugangsdaten enthalten"
    )


def _pruefe_eintraege(eintraege: list) -> list[dict]:
    """Prüft alle Einträge, bevor irgendein Schlüssel aufgelöst wird.

    Rückgabe: je Eintrag die geprüften Felder; der Aufrufer löst daraus die Schlüssel auf.
    """
    geprueft: list[dict] = []
    namen: set[str] = set()
    for nr, eintrag in enumerate(eintraege, start=1):
        if not isinstance(eintrag, dict):
            raise ProviderFehler(f"Eintrag {nr}: Eintrag muss ein Objekt sein")
        if "api_key" in eintrag:
            raise ProviderFehler(
                f"Eintrag {nr}: Feld 'api_key' ist nicht erlaubt, "
                "Schlüssel gehören in eine Umgebungsvariable"
            )
        name = _pflicht_text(eintrag, "name", nr)
        if not _NAME.fullmatch(name):
            raise ProviderFehler(
                f"Eintrag {nr}: Feld 'name' darf nur A-Z, a-z, 0-9, '.', '_', ':' und '-' "
                "enthalten"
            )
        if name in namen:
            raise ProviderFehler(f"Eintrag {nr}: Feld 'name' ist doppelt")
        namen.add(name)

        art = _pflicht_text(eintrag, "art", nr)
        if art == _ART_ALIAS:
            geprueft.append(
                {"name": name, "art": art, "alias": _pflicht_text(eintrag, "alias", nr)}
            )
        elif art == _ART_OPENAI:
            extra_body = eintrag.get("extra_body", {})
            if not isinstance(extra_body, dict):
                raise ProviderFehler(f"Eintrag {nr}: Feld 'extra_body' muss ein Objekt sein")
            umgebung = _pflicht_text(eintrag, "api_key_umgebung", nr)
            if not _UMGEBUNG.fullmatch(umgebung):
                raise ProviderFehler(
                    f"Eintrag {nr}: Feld 'api_key_umgebung' muss der Form [A-Z][A-Z0-9_]* "
                    "entsprechen"
                )
            geprueft.append(
                {
                    "name": name,
                    "art": art,
                    "base_url": _base_url(eintrag, nr),
                    "modell": _pflicht_text(eintrag, "modell", nr),
                    "api_key_umgebung": umgebung,
                    "extra_body": dict(extra_body),
                }
            )
        else:
            raise ProviderFehler(f"Eintrag {nr}: Feld 'art' ist unbekannt")
    return geprueft


def _lies(pfad: str) -> list:
    try:
        with open(pfad, encoding="utf-8") as datei:
            roh = json.load(datei)
    except OSError:
        raise ProviderFehler(f"Providerliste {pfad} ist nicht lesbar") from None
    except ValueError:
        raise ProviderFehler(f"Providerliste {pfad} ist kein JSON") from None
    if not isinstance(roh, dict):
        raise ProviderFehler("Wurzel der Providerliste muss ein Objekt sein")
    eintraege = roh.get("providers")
    if not isinstance(eintraege, list):
        raise ProviderFehler("Feld 'providers' muss eine Liste sein")
    return eintraege


def _alias_provider(name: str, alias: str, rolle: str, env: Mapping[str, str]) -> Provider | None:
    """LiteLLM-Alias als Provider; None, wenn Adresse oder Benchmark-Schlüssel fehlt."""
    basis = env.get("LITELLM", "")
    schluessel = env.get("LITELLM_BENCHMARK_KEY", "")
    if not basis or not schluessel:
        return None
    return Provider(
        name=name,
        art=_ART_ALIAS,
        base_url=f"{basis.rstrip('/')}/v1",
        modell=alias,
        api_key=schluessel,
        extra_body={"disable_fallbacks": True},
        rolle=rolle,
    )


def load_providers(
    pfad: str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[list[Provider], list[dict]]:
    """Liest die Providerliste und löst die Schlüssel aus `env` auf (None = os.environ).

    Pfad: Argument `pfad`, sonst `env["AGENT_BENCHMARK_PROVIDERS"]`, sonst `DEFAULT_PFAD`.
    Rückgabe: (aktive Provider in Dateireihenfolge, übersprungene als {"name", "grund"}).
    Fehler in der Datei selbst werfen `ProviderFehler`; ein fehlender Schlüssel ist keiner.
    """
    env = os.environ if env is None else env
    datei = pfad if pfad is not None else env.get("AGENT_BENCHMARK_PROVIDERS") or DEFAULT_PFAD

    aktiv: list[Provider] = []
    uebersprungen: list[dict] = []
    for eintrag in _pruefe_eintraege(_lies(datei)):
        if eintrag["art"] == _ART_ALIAS:
            provider = _alias_provider(eintrag["name"], eintrag["alias"], "", env)
            grund = _LITELLM_GRUND
        else:
            umgebung = eintrag["api_key_umgebung"]
            schluessel = env.get(umgebung, "")
            grund = f"{umgebung} nicht gesetzt"
            provider = None
            if schluessel:
                provider = Provider(
                    name=eintrag["name"],
                    art=_ART_OPENAI,
                    base_url=eintrag["base_url"],
                    modell=eintrag["modell"],
                    api_key=schluessel,
                    extra_body=eintrag["extra_body"],
                )
        if provider is None:
            uebersprungen.append({"name": eintrag["name"], "grund": grund})
        else:
            aktiv.append(provider)
    return aktiv, uebersprungen


def aktuelle_provider(
    wert: Callable[[str, str], str],
    env: Mapping[str, str] | None = None,
) -> tuple[list[Provider], list[dict]]:
    """Provider für die aktuell eingestellten Worker- und Review-Aliase.

    `wert(name, vorgabe)` ersetzt `agent_settings.value`; das Modul importiert es nicht.
    Mehrere Rollen mit demselben Alias ergeben einen Eintrag mit „+“ verbundenen Rollen.
    """
    env = os.environ if env is None else env
    review = env.get("REVIEW_MODEL", "reviewer-go")
    rollen: dict[str, list[str]] = {}
    for rolle, alias in (
        ("worker", wert("LITELLM_MODEL", env.get("LITELLM_MODEL", "coder"))),
        ("review", review),
        ("review_gross", wert("REVIEW_MODEL_BIG", review)),
    ):
        rollen.setdefault(alias, []).append(rolle)

    aktiv: list[Provider] = []
    uebersprungen: list[dict] = []
    for alias, alias_rollen in rollen.items():
        provider = _alias_provider(alias, alias, "+".join(alias_rollen), env)
        if provider is None:
            uebersprungen.append({"name": alias, "grund": _LITELLM_GRUND})
        else:
            aktiv.append(provider)
    return aktiv, uebersprungen
