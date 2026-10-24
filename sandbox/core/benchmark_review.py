"""Review-Aufgaben für den Benchmark und deterministische Bewertung ohne Richtermodell (#336).

Teil 4 von 5 des Benchmark-Moduls. Der Katalog enthält vier kleine Diffs: drei mit je einem
eingebauten Fehler und einen sauberen Diff, der grundloses „Änderungen nötig“ aufdeckt. Das
geprüfte Modell antwortet als Reviewer; bewertet wird allein mit `parse_urteil()` und
`score_review()` — kein zweites Modell, kein Netz, kein Modellaufruf.

Das Urteilsformat entspricht der Konvention in `AGENTS.md` (Reviewer, Punkt 3): erste Zeile
das Urteil, danach die Begründung. Die Merkmale sind Regex-Gruppen je Fehlerart; eine Gruppe
gilt als erfüllt, wenn mindestens eine ihrer Regexe in der Begründung trifft.

Regel 5: Die Diffs enthalten nur Platzhalter (Variablennamen, Umgebungsvariablen), nie echt
aussehende Schlüssel, Tokens oder Passwörter — das Repository ist öffentlich.
"""
from __future__ import annotations

import re

URTEIL_FREIGEGEBEN = "Freigegeben"
URTEIL_AENDERUNGEN = "Änderungen nötig"

# Schreibweisen nach der Umlaut-Ersatzschreibung; verglichen wird in Kleinschreibung.
_WORT_FREIGEGEBEN = "freigegeben"
_WORT_AENDERUNGEN = "aenderungen noetig"

_ZIERRAT = "*_#`\"'„“”«»"
_SCHLUSSPUNKTE = ".!:"
_UMLAUTE = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}

REVIEW_TASKS: list[dict] = [
    {
        "id": "off_by_one",
        "titel": "Summenfunktion",
        "diff": """diff --git a/sandbox/zaehler.py b/sandbox/zaehler.py
--- a/sandbox/zaehler.py
+++ b/sandbox/zaehler.py
@@ -1,6 +1,8 @@
 def summe(werte: list[int]) -> int:
     gesamt = 0
-    for wert in werte:
+    for i in range(len(werte) - 1):
+        wert = werte[i]
         gesamt += wert
     return gesamt
""",
        "erwartet": URTEIL_AENDERUNGEN,
        "merkmale": [
            [r"letzte[sn]? (element|wert|eintrag)", r"off.by.one", r"letzter (wert|eintrag)"],
            [r"len\([^)]*\)\s*-\s*1", r"grenze", r"obergrenze"],
        ],
        "referenz_antwort": """Änderungen nötig
Die Schleife endet bei `len(werte) - 1`. Weil die Obergrenze von `range` exklusiv ist, fehlt
dadurch das letzte Element der Liste. Bitte die Grenze auf `len(werte)` setzen und einen Test
mit dem letzten Wert ergänzen.""",
    },
    {
        "id": "secret_in_fehler",
        "titel": "HTTP-Abruf",
        "diff": """diff --git a/sandbox/abruf.py b/sandbox/abruf.py
--- a/sandbox/abruf.py
+++ b/sandbox/abruf.py
@@ -6,7 +6,9 @@ def hole(url: str) -> dict:
     kopf = {"Authorization": f"Bearer {os.environ['ABRUF_TOKEN']}"}
     antwort = client.get(url, headers=kopf, timeout=10)
     if antwort.status_code != 200:
-        raise AbrufFehler(f"Abruf fehlgeschlagen: {antwort.status_code}")
+        raise AbrufFehler(
+            f"Abruf fehlgeschlagen: {antwort.status_code} bei {url} mit {kopf}"
+        )
     return antwort.json()
""",
        "erwartet": URTEIL_AENDERUNGEN,
        "merkmale": [
            [r"kopfzeile", r"header", r"authorization", r"zugangsdaten", r"anmeldedaten"],
            [r"fehlermeldung", r"log", r"ausgabe", r"meldung"],
            [r"regel 5", r"geheim", r"schlüssel", r"schluessel", r"token"],
        ],
        "referenz_antwort": """Änderungen nötig
Die Fehlermeldung hängt die vollständigen Kopfzeilen samt Authorization an den Text an. Damit
landen Zugangsdaten im Fehlertext und im Log (Regel 5). Bitte nur den Statuscode nennen und
Kopfzeilen niemals ausgeben.""",
    },
    {
        "id": "stiller_fehler",
        "titel": "Datei speichern",
        "diff": """diff --git a/sandbox/speichern.py b/sandbox/speichern.py
--- a/sandbox/speichern.py
+++ b/sandbox/speichern.py
@@ -8,6 +8,8 @@ def sichere(pfad: str, text: str) -> None:
     try:
         with open(pfad, "w", encoding="utf-8") as datei:
             datei.write(text)
+    except Exception:
+        pass
""",
        "erwartet": URTEIL_AENDERUNGEN,
        "merkmale": [
            [r"verschluck", r"ignoriert", r"unterdrück", r"unterdrueck"],
            [r"except", r"exception", r"pass"],
            [r"regel 7", r"stumm", r"still", r"ohne meldung"],
        ],
        "referenz_antwort": """Änderungen nötig
Der neue Block `except Exception: pass` verschluckt jeden Fehler beim Schreiben, ohne Meldung
und ohne Logeintrag (Regel 7). Ein fehlgeschlagenes Speichern bleibt damit unsichtbar. Bitte
den Fehler weiterreichen oder wenigstens protokollieren.""",
    },
    {
        "id": "sauber",
        "titel": "Kleine Korrektur",
        "diff": """diff --git a/sandbox/runden.py b/sandbox/runden.py
--- a/sandbox/runden.py
+++ b/sandbox/runden.py
@@ -1,2 +1,2 @@
 def auf_zwei_stellen(wert: float) -> float:
-    return wert
+    return round(wert, 2)
diff --git a/tests/test_runden.py b/tests/test_runden.py
--- a/tests/test_runden.py
+++ b/tests/test_runden.py
@@ -1,3 +1,6 @@
 from sandbox.runden import auf_zwei_stellen
 
+
+def test_rundet_auf_zwei_stellen():
+    assert auf_zwei_stellen(1.234) == 1.23
""",
        "erwartet": URTEIL_FREIGEGEBEN,
        "merkmale": [],
        "referenz_antwort": """Freigegeben
Die Änderung rundet korrekt auf zwei Stellen und bringt einen passenden Test mit. Keine
Auffälligkeiten.""",
    },
]

_PROMPT = """Du bist Reviewer für einen Pull Request in einem Python-Projekt.
Prüfe den folgenden Diff.

{diff}

Antworte in zwei Teilen: In der ersten Zeile steht ausschließlich dein Urteil, entweder
"{frei}" oder "{aend}". Ab der zweiten Zeile folgt eine kurze Begründung mit den gefundenen
Mängeln."""


def build_review_prompt(task: dict) -> str:
    """Prompt mit eingebettetem Diff und fester Antwortvorgabe.

    Der Prompt nennt nur den Diff und das Urteilsformat. Titel, Merkmale und Referenzantwort
    der Aufgabe bleiben draußen, damit der Prompt die Lösung nicht verrät.
    """
    return _PROMPT.format(
        diff=task["diff"], frei=URTEIL_FREIGEGEBEN, aend=URTEIL_AENDERUNGEN
    )


def _normalisiere(zeile: str) -> str:
    """Entfernt Markdown-Zierrat, Schlusspunkte und Umlaute für den Urteilsvergleich."""
    for zeichen in _ZIERRAT:
        zeile = zeile.replace(zeichen, "")
    zeile = zeile.strip().rstrip(_SCHLUSSPUNKTE).strip().lower()
    for umlaut, ersatz in _UMLAUTE.items():
        zeile = zeile.replace(umlaut, ersatz)
    return zeile


def _beginnt_mit(text: str, wort: str) -> bool:
    """Wortanfang: danach darf kein Buchstabe und keine Ziffer folgen."""
    if not text.startswith(wort):
        return False
    rest = text[len(wort):]
    return not rest or not rest[0].isalnum()


def _kommt_vor(text: str, wort: str) -> bool:
    """Wort kommt irgendwo vor; angrenzende Buchstaben oder Ziffern verhindern den Treffer."""
    return re.search(rf"(?<![a-z0-9]){re.escape(wort)}(?![a-z0-9])", text) is not None


def parse_urteil(antwort: str) -> str | None:
    """Urteil aus der ersten nicht leeren Zeile; alles andere ergibt None.

    Verglichen wird ohne Zierrat, Schlusspunkt, Rücksicht auf Groß-/Kleinschreibung und mit
    Umlaut-Ersatzschreibung. Nennt die Zeile beide Urteile, ist sie kein Urteil.
    """
    if not isinstance(antwort, str):
        return None
    zeile = next((z for z in antwort.splitlines() if z.strip()), None)
    if zeile is None:
        return None
    text = _normalisiere(zeile)
    if _kommt_vor(text, _WORT_FREIGEGEBEN) and _kommt_vor(text, _WORT_AENDERUNGEN):
        return None
    treffer = [
        urteil
        for wort, urteil in (
            (_WORT_FREIGEGEBEN, URTEIL_FREIGEGEBEN),
            (_WORT_AENDERUNGEN, URTEIL_AENDERUNGEN),
        )
        if _beginnt_mit(text, wort)
    ]
    return treffer[0] if len(treffer) == 1 else None


def _nach_urteilszeile(antwort: str) -> str:
    """Begründungsteil: alles nach der ersten nicht leeren Zeile, sonst leerer Text."""
    zeilen = antwort.splitlines()
    for nummer, zeile in enumerate(zeilen):
        if zeile.strip():
            return "\n".join(zeilen[nummer + 1:])
    return ""


def _gruppe_trifft(gruppe, text: str) -> bool:
    """Eine Gruppe gilt als erfüllt, wenn mindestens eine ihrer Regexe trifft.

    Nicht kompilierbare Muster und Nicht-Text-Einträge zählen als Nichttreffer, damit die
    Bewertung nie wirft.
    """
    if not isinstance(gruppe, (list, tuple)):
        return False
    for rx in gruppe:
        try:
            muster = re.compile(rx, re.IGNORECASE)
            if muster.search(text):
                return True
        except Exception:
            continue
    return False


def score_review(task: dict, antwort: str) -> dict:
    """Bewertet `antwort` gegen `task`; Ergebnis {"bestanden": bool, "grund": str | None}.

    Reihenfolge: kein Urteil, falsches Urteil, bei fehlerhaften Diffs die Merkmale in der
    Begründung (die Urteilszeile selbst zählt nicht). Wirft nie.
    """
    if not isinstance(task, dict):
        task = {}
    urteil = parse_urteil(antwort)
    if urteil is None:
        return {"bestanden": False, "grund": "kein Urteil"}
    if urteil != task.get("erwartet"):
        return {"bestanden": False, "grund": "falsches Urteil"}
    if urteil == URTEIL_AENDERUNGEN:
        gruppen = task.get("merkmale") or []
        if not isinstance(gruppen, (list, tuple)):
            gruppen = []
        text = _nach_urteilszeile(antwort)
        for gruppe in gruppen:
            if not _gruppe_trifft(gruppe, text):
                return {"bestanden": False, "grund": "Fehler nicht erkannt"}
    return {"bestanden": True, "grund": None}
