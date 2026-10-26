"""Benchmark-Runner: Anbieter durch den Aufgabenkatalog schicken und Ergebnisse sammeln (#337).

Teil 5 von 5 des Benchmark-Moduls. Dieses Skript verbindet die vier Module aus `sandbox/core/`
zu einem Lauf von Hand: Es ruft je Anbieter alle Code- und Review-Aufgaben auf, bewertet die
Antworten deterministisch (`benchmark_code`, `benchmark_review`), hängt jede Zeile an eine
JSONL-Datei und druckt eine Zusammenfassung. Dashboard, Timer und systemd-Unit folgen in Phase 2.

Der Benchmark berät nur: Er ändert keine Einstellung (`LITELLM_MODEL`, `REVIEW_MODEL`), schreibt
weder ins Worker-Log noch ins Audit-Log und läuft nie parallel. Regel 5: Schlüssel kommen aus
der Umgebung, stehen nie in einer Zeile und erscheinen in keiner Ausgabe.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone

# Als Skript gestartet liegt `sandbox/` im Suchpfad, nicht das Repository; für `sandbox.core`
# und `agent_settings` muss deshalb die Wurzel dazu.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox import agent_settings  # noqa: E402
from sandbox.core import (  # noqa: E402
    benchmark_code,
    benchmark_providers,
    benchmark_review,
    openai_compat_client,
)
from sandbox.core.benchmark_providers import Provider  # noqa: E402

ERGEBNIS_PFAD = "/root/benchmark-ergebnisse.jsonl"

LOCK_PFADE = [
    "/run/qwen-worker-w*.lock",
    "/run/qwen-worker.lock",
    "/run/qwen-review.lock",
]


def ist_slot_belegt(pfade: Sequence[str] | None = None) -> bool:
    """Prüft, ob aktive Lock-Dateien für Worker- oder Review-Slots existieren."""
    pfad_muster = LOCK_PFADE if pfade is None else pfade
    for muster in pfad_muster:
        treffer = glob.glob(muster)
        if treffer:
            return True
    return False


GRUND_RATENBEGRENZT = "ratenbegrenzt"


def _jetzt_utc() -> str:
    """Zeitstempel in UTC, sekundengenau, mit `Z` statt `+00:00`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rufe(
    chat: Callable[..., dict],
    anbieter: Provider,
    prompt: str,
    timeout_s: float,
) -> dict:
    """Genau der eine Aufruf je Versuch; `extra_body` trägt z. B. `disable_fallbacks`."""
    return chat(
        anbieter.base_url,
        anbieter.modell,
        prompt,
        api_key=anbieter.api_key,
        timeout=timeout_s,
        extra_body=anbieter.extra_body,
    )


def _bewerte(
    aufgabe: dict,
    ergebnis: dict,
    bewerten: Callable[[dict, str], dict],
) -> tuple[bool, str | None, bool]:
    """(bestanden, grund, ratenbegrenzt) aus dem Ergebnis des letzten Aufrufs.

    Eine Antwort, die nach dem 429-Wiederholungsversuch immer noch ratenbegrenzt ist, zählt
    weder als bestanden noch als gewertet; ein anderer Fehler wird wie ohne Wiederholung
    durchgereicht.
    """
    if ergebnis.get("ok"):
        bewertung = bewerten(aufgabe, ergebnis.get("text") or "")
        return bool(bewertung.get("bestanden")), bewertung.get("grund"), False
    if ergebnis.get("ratenbegrenzt"):
        return False, GRUND_RATENBEGRENZT, True
    return False, ergebnis.get("fehler"), False


def run_benchmark(
    providers: Sequence[Provider],
    code_aufgaben: Sequence[dict],
    review_aufgaben: Sequence[dict],
    chat: Callable[..., dict],
    wiederholungen: int = 3,
    gesamt_limit_s: float = 1800.0,
    aufruf_timeout_s: float = 120.0,
    pause_429_s: float = 20.0,
    uhr: Callable[[], float] = time.monotonic,
    schlafen: Callable[[float], None] = time.sleep,
    jetzt: Callable[[], str] = _jetzt_utc,
    lauf_id: str = "",
) -> tuple[list[dict], bool]:
    """Führt den Katalog je Anbieter nacheinander aus und gibt (Zeilen, abgebrochen) zurück.

    Reihenfolge: je Anbieter zuerst alle Code-Aufgaben, dann alle Review-Aufgaben; je Aufgabe
    `wiederholungen` Versuche (`versuch` zählt ab 1). `uhr` wird einmal zu Beginn und einmal vor
    jedem Versuch gelesen — nicht vor dem Wiederholungsversuch nach 429, damit die Pause nicht
    als eigener Versuch zählt. Ist das Gesamtlimit erreicht, endet der Lauf sofort; die bisherigen
    Zeilen bleiben erhalten.
    """
    if wiederholungen < 1:
        raise ValueError("wiederholungen muss mindestens 1 sein")

    beginn = uhr()
    lauf = lauf_id or jetzt()
    zeilen: list[dict] = []
    aufgaben = (
        ("code", code_aufgaben, benchmark_code.build_code_prompt, benchmark_code.score_code),
        ("review", review_aufgaben, benchmark_review.build_review_prompt,
         benchmark_review.score_review),
    )

    for anbieter in providers:
        for art, katalog, prompt_bauen, bewerten in aufgaben:
            for aufgabe in katalog:
                prompt = prompt_bauen(aufgabe)
                for versuch in range(1, wiederholungen + 1):
                    if uhr() - beginn >= gesamt_limit_s:
                        return zeilen, True
                    ergebnis = _rufe(chat, anbieter, prompt, aufruf_timeout_s)
                    if not ergebnis.get("ok") and ergebnis.get("ratenbegrenzt"):
                        schlafen(pause_429_s)
                        ergebnis = _rufe(chat, anbieter, prompt, aufruf_timeout_s)
                    bestanden, grund, ratenbegrenzt = _bewerte(aufgabe, ergebnis, bewerten)
                    zeilen.append(
                        {
                            "lauf": lauf,
                            "zeit": jetzt(),
                            "anbieter": anbieter.name,
                            "rolle": anbieter.rolle,
                            "aufgabe": aufgabe.get("id"),
                            "art": art,
                            "versuch": versuch,
                            "bestanden": bestanden,
                            "grund": grund,
                            "ratenbegrenzt": ratenbegrenzt,
                            "dauer_s": ergebnis.get("dauer_s"),
                            "kosten": ergebnis.get("kosten"),
                        }
                    )
    return zeilen, False


def zusammenfassung(zeilen: Sequence[dict]) -> dict[str, dict[str, dict]]:
    """{anbieter: {art: {"bestanden", "gewertet", "ratenbegrenzt", "quote"}}}.

    Ratenbegrenzte Zeilen zählen nur unter "ratenbegrenzt", nicht als gewertet: Ein vom Anbieter
    abgelehnter Aufruf sagt nichts über die Fähigkeit des Modells. Nur Arten mit Zeilen erscheinen.
    """
    ergebnis: dict[str, dict[str, dict]] = {}
    for zeile in zeilen:
        arten = ergebnis.setdefault(zeile.get("anbieter"), {})
        werte = arten.setdefault(
            zeile.get("art"),
            {"bestanden": 0, "gewertet": 0, "ratenbegrenzt": 0, "quote": None},
        )
        if zeile.get("ratenbegrenzt"):
            werte["ratenbegrenzt"] += 1
            continue
        werte["gewertet"] += 1
        if zeile.get("bestanden"):
            werte["bestanden"] += 1
    for arten in ergebnis.values():
        for werte in arten.values():
            werte["quote"] = (
                werte["bestanden"] / werte["gewertet"] if werte["gewertet"] else None
            )
    return ergebnis


def append_ergebnisse(pfad: str, zeilen: Sequence[dict]) -> None:
    """Hängt je Zeile ein JSON-Objekt samt Zeilenumbruch an `pfad` an (UTF-8, ohne Escapes).

    Eine leere Liste schreibt nichts und legt auch keine Datei an. Ein fehlendes Verzeichnis
    oder ein anderer Schreibfehler bleibt ein `OSError` beim Aufrufer — still verschluckt würde
    der Lauf Ergebnisse verlieren, ohne dass es jemand merkt (Regel 7).
    """
    if not zeilen:
        return
    with open(pfad, "a", encoding="utf-8", newline="\n") as datei:
        for zeile in zeilen:
            datei.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def _wiederholungen(text: str) -> int:
    """Wert für --wiederholungen: ganze Zahl von 1 bis 10."""
    try:
        wert = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("muss eine ganze Zahl sein") from None
    if not 1 <= wert <= 10:
        raise argparse.ArgumentTypeError("muss zwischen 1 und 10 liegen")
    return wert


def _argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark.py",
        description="Benchmark: aktive Anbieter durch den Aufgabenkatalog schicken.",
    )
    parser.add_argument(
        "--providers",
        metavar="PFAD",
        help="Providerliste (JSON); Vorgabe: AGENT_BENCHMARK_PROVIDERS, sonst "
        "/etc/agent-benchmark-providers.json",
    )
    parser.add_argument(
        "--out",
        metavar="PFAD",
        help="Ergebnisdatei (JSONL); Vorgabe: BENCHMARK_ERGEBNISSE, sonst " + ERGEBNIS_PFAD,
    )
    parser.add_argument(
        "--wiederholungen",
        metavar="N",
        type=_wiederholungen,
        default=3,
        help="Versuche je Aufgabe, 1 bis 10 (Vorgabe 3)",
    )
    parser.add_argument(
        "--nur",
        metavar="NAME",
        action="append",
        default=[],
        help="nur diese Anbieter messen (wiederholbar)",
    )
    parser.add_argument(
        "--ohne-aktuelle",
        action="store_true",
        help="aktuelle Worker-/Review-Aliase nicht aufnehmen",
    )
    parser.add_argument(
        "--nur-aktuelle",
        action="store_true",
        help="nur aktuelle Worker-/Review-Aliase, keine Providerliste lesen",
    )
    parser.add_argument(
        "--wenn-frei",
        action="store_true",
        help="überspringt den Lauf, wenn ein Worker- oder Review-Slot belegt ist",
    )
    return parser


def _anbieter(
    args: argparse.Namespace,
    env: Mapping[str, str],
) -> tuple[list[Provider], list[dict]]:
    """Aktive und übersprungene Anbieter: erst die Liste, dann die aktuellen Aliase.

    Bei gleichem Namen gewinnt der Eintrag der Liste; ein übersprungener Listeneintrag
    unterdrückt keinen gleichnamigen aktiven Alias, weil dort ein anderer Schlüssel gilt.
    """
    aktiv: list[Provider] = []
    uebersprungen: list[dict] = []
    if not args.nur_aktuelle:
        liste, fehlend = benchmark_providers.load_providers(args.providers, env)
        aktiv.extend(liste)
        uebersprungen.extend(fehlend)
    if not args.ohne_aktuelle:
        aktuelle, fehlend = benchmark_providers.aktuelle_provider(agent_settings.value, env)
        bekannt = {anbieter.name for anbieter in aktiv}
        aktiv.extend(anbieter for anbieter in aktuelle if anbieter.name not in bekannt)
        bekannt = {eintrag["name"] for eintrag in uebersprungen}
        uebersprungen.extend(eintrag for eintrag in fehlend if eintrag["name"] not in bekannt)
    return aktiv, uebersprungen


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    chat: Callable[..., dict] | None = None,
    schlafen: Callable[[float], None] | None = None,
) -> int:
    """Kommandozeile: 0 Erfolg, 1 kein Anbieter oder Datei nicht schreibbar, 2 Providerfehler."""
    env = os.environ if env is None else env
    chat = openai_compat_client.chat if chat is None else chat
    schlafen = time.sleep if schlafen is None else schlafen

    parser = _argparser()
    args = parser.parse_args(argv)
    if args.nur_aktuelle and (args.providers or args.ohne_aktuelle):
        parser.error("--nur-aktuelle schließt --providers und --ohne-aktuelle aus")

    if args.wenn_frei or env.get("BENCHMARK_WENN_FREI") == "1":
        if ist_slot_belegt():
            print("Benchmark übersprungen: Slot aktiv.")
            return 0

    try:
        aktiv, uebersprungen = _anbieter(args, env)
    except benchmark_providers.ProviderFehler as fehler:
        print(f"Providerliste ungültig: {fehler}", file=sys.stderr)
        return 2

    if args.nur:
        vorhanden = {anbieter.name for anbieter in aktiv}
        vorhanden.update(eintrag["name"] for eintrag in uebersprungen)
        for name in args.nur:
            if name not in vorhanden:
                print(f"unbekannter Anbieter: {name}", file=sys.stderr)
                return 2
        gewuenscht = set(args.nur)
        aktiv = [anbieter for anbieter in aktiv if anbieter.name in gewuenscht]
        uebersprungen = [e for e in uebersprungen if e["name"] in gewuenscht]

    for eintrag in uebersprungen:
        print(f"übersprungen: {eintrag['name']} ({eintrag['grund']})")

    if not aktiv:
        print("keine aktiven Anbieter", file=sys.stderr)
        return 1

    pfad = args.out or env.get("BENCHMARK_ERGEBNISSE") or ERGEBNIS_PFAD
    zeilen, abgebrochen = run_benchmark(
        aktiv,
        benchmark_code.CODE_TASKS,
        benchmark_review.REVIEW_TASKS,
        chat,
        wiederholungen=args.wiederholungen,
        schlafen=schlafen,
    )

    zusammen = zusammenfassung(zeilen)
    for anbieter in aktiv:
        arten = zusammen.get(anbieter.name, {})
        code = arten.get("code", {})
        review = arten.get("review", {})
        ratenbegrenzt = code.get("ratenbegrenzt", 0) + review.get("ratenbegrenzt", 0)
        print(
            f"{anbieter.name}: code {code.get('bestanden', 0)}/{code.get('gewertet', 0)} "
            f"review {review.get('bestanden', 0)}/{review.get('gewertet', 0)} "
            f"ratenbegrenzt {ratenbegrenzt}"
        )

    try:
        append_ergebnisse(pfad, zeilen)
    except OSError as fehler:
        print(f"Ergebnisdatei nicht schreibbar: {type(fehler).__name__}", file=sys.stderr)
        return 1

    print(f"Ergebnisse: {pfad} ({len(zeilen)} Zeilen)")
    if abgebrochen:
        print("Gesamtlimit erreicht, Lauf unvollständig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
