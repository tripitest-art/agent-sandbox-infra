# Planer-Ablauf und Agenten-Labels

Für jeden Planer, gleich welches Modell. Die Regeln stehen in [`AGENTS.md`](../AGENTS.md)
(Worker-Aufgaben → Planer); hier steht, wie man zu einem Issue kommt, das ein anderes Modell ohne
Rückfragen umsetzen kann. Diese Datei ist aus `ENTWICKLUNG.md` in `tripitest-art/sc-digger`
übernommen und auf dieses Repository angepasst — Beispiele und Test-Helfer stammen von hier.

Der Planer schreibt Issues, keinen Code: keine Branches, keine Commits, keine PRs, kein Merge.

## Planer-Ablauf

0. **Backlog:** Ideen ohne Akzeptanztest stehen als Issue ohne `bereit` (`ROADMAP.md`). Der Planer
   macht daraus erst dann ein `worker-task`, wenn die offenen Fragen beantwortet sind.
1. **Auftrag klären.** Aus dem Gespräch mit Stephan oder aus `ROADMAP.md`. Ein Thema pro Issue;
   ist es größer als ein PR, in Teile schneiden („Teil 1 von #58"). Offene Fachfragen
   (Schwellwerte, Verhalten im Fehlerfall) Stephan stellen, nicht raten.
2. **Stand prüfen.** Offene Issues und PRs nach Dubletten und nach denselben Dateien durchsuchen
   (Regel 8). Überschneidung → `blockiert` und eine Zeile `Wartet auf: #X` (siehe `AGENTS.md`).
   Bestehende Akzeptanztests suchen, die die geänderten Funktionen prüfen
   (`git grep <funktion> tests/acceptance`). Worker dürfen sie nicht ändern; die Aufgabe muss so
   geschnitten sein, dass sie grün bleiben.
   **Achtung:** Enthält der Issue-Text in einer Zeile die Wendung `Wartet auf:` und danach eine
   Zahl (auch als Beispiel im Akzeptanztest), liest der Review-Takt das als echte Abhängigkeit —
   das Issue wird dann nie automatisch freigegeben. Beispielnummern deshalb zusammensetzen oder
   außerhalb einer solchen Zeile nennen (siehe #46).
3. **Code lesen.** Jede Datei, die das Issue nennt, auf dem Stand von `main`. Bestehende Helfer
   und Fakes wiederverwenden, statt dem Worker neue vorzuschreiben — hier sind das `FakeGH` in
   [`tests/test_worker_tick.py`](../tests/test_worker_tick.py) und das `_load()`-Muster, mit dem
   [`tests/test_acceptance_guard.py`](../tests/test_acceptance_guard.py) die Takt-Skripte per Pfad
   lädt. Für die Projektliste gibt es `tests/test_projects.py`.
4. **Issue-Text schreiben** in der Form, die das Formular erzeugt: Überschriften `### Ziel`,
   `### Betroffene Dateien`, `### Schnittstellen`, `### Nicht Teil dieser Aufgabe`,
   `### Akzeptanztests`, `### Fertig, wenn`, `### Berührt …` (Hotspot-Abschnitt des Formulars),
   `### Merge-Modus`, `### Kontext`, in dieser Reihenfolge. Dropdowns als Klartext mit dem
   genauen Optionstext.
   - **Betroffene Dateien:** vollständig, mit Funktion und Stelle. Was fehlt, darf der Worker
     nicht anfassen. Immer dabei: `tests/acceptance/test_issue_<N>.py` und die eigene Testdatei.
     Bestehende Tests, deren Erwartung sich ändert, mit Datei und Stelle nennen; sonst darf der
     Worker sie nicht anpassen.
   - **Schnittstellen:** exakte Signaturen mit Typen, Rückgabe, Verhalten bei leer/`None`/Fehler,
     Log-Meldungen im Wortlaut, wenn Tests sie prüfen. In einem ```` ```python ````-Block.
   - **Nicht Teil dieser Aufgabe:** das naheliegende „gleich mit aufräumen" ausschließen.
   - **Hotspot-Abschnitt:** in diesem Repository immer
     `nein (Infra-Repo, kein Hotspot)` — die Überschrift stammt aus dem Formular der
     Projektvorlage und nennt dort die Hauptdatei des anderen Projekts.
   - **Merge-Modus:** `manuell durch Stephan (Betrieb, Server, Zugangsdaten)` für alles, was
     Betrieb oder Aufsicht betrifft, und für jedes Issue, das eine Datei aus `manual_paths` berührt
     (Vorschlag: `sandbox/*`, `.github/*`, `AGENTS.md`, `tests/acceptance/*`, `skills/*`). `automatisch`
     nur für Issues, die ausschließlich `docs/*`, `README.md`, `CHANGELOG.md`, `ROADMAP.md` oder
     eigene Testdateien ändern. Im Zweifel manuell. Der Review-Takt prüft die Dateiliste zusätzlich selbst;
     deployt wird nie automatisch (Regel 4).
5. **Akzeptanztests** in **genau einem** ```` ```python ````-Block direkt unter
   `### Akzeptanztests` (so liest ihn `acceptance-guard`). Sie
   - prüfen nur die Schnittstellen aus dem Issue, keine Interna;
   - laufen ohne Netzwerk, ohne `gh` und ohne Server (die Takt-Skripte lädt man wie in
     `tests/test_worker_tick.py` per Pfad), ohne Uhrzeit und Zufall, ohne `skip`/`xfail`;
   - importieren nur die Standardbibliothek und die Takt-Module dieses Repos;
   - schreiben nur in `tmp_path`. **Nicht** nach `/root` — dort ist der Pfad auf dem
     Entwicklungsrechner nicht schreibbar und in der CI nicht erlaubt (Fehler aus #39).
6. **Akzeptanztests prüfen (mit Shell).** Datei als Probe unter `tests/acceptance/` anlegen,
   `python -m pytest -q -p no:cacheprovider <probe>` laufen lassen und belegen, dass jeder Test
   **rot** ist und zwar aus dem gemeinten Grund; danach die Probe löschen. Ein eigenes
   Prüfwerkzeug für Akzeptanztests gibt es in diesem Repository nicht (in sc-digger heißt es
   `issuecheck`). Ergebnis mit dem Commit-Hash von `main` in den
   Kontext („ohne Umsetzung 3 von 4 rot, mit Umsetzung grün").
   **Ohne Shell** geht das nicht: Label `entwurf` statt `bereit`, im Kontext „Akzeptanztests noch
   nicht ausgeführt (Planer ohne Shell)". Stephan oder ein Agent mit Shell prüft sie und ersetzt
   `entwurf` durch `bereit`.
7. **Entwurf zeigen.** Titel, Text und Labels Stephan im Chat vorlegen; erst nach seinem OK
   anlegen (außer er hat ausdrücklich „direkt anlegen" gesagt).
8. **Anlegen** mit Labels `worker-task`, `phase-N`, `bereit` (ohne Shell `entwurf`), genau einem
   `agent-<familie>` (Abschnitt „Agenten-Labels"), bei Bedarf zusätzlich `blockiert`. Danach
   Nummer und Link melden. Im Issue-Text `test_issue_<N>` stehen lassen; die Nummer kennt der
   Worker.

Nachträglich ändern darf der Planer ein Issue nur, solange es nicht `in-arbeit` ist. Danach nur
noch kommentieren. Muss ein Akzeptanztest korrigiert werden: Issue korrigieren; der PR braucht
dann `freigabe-geschützt` (`AGENTS.md`, Worker 5).

## Agenten-Labels

Der Planer gibt jedem Worker-Issue genau ein Label, welche Modellfamilie es umsetzt. Worker nehmen
nur Issues mit ihrem Label (`AGENTS.md`, Autonome Auswahl). Der Reviewer stammt aus einer anderen
Familie (Regel 6).

| Label | Worker | Passende Issues |
|---|---|---|
| `agent-qwen` | Sandbox-Worker (OpenCode/Qwen Code; Modell aktuell DeepSeek über LiteLLM, früher Qwen lokal) | Issues mit genauer Schnittstelle und Akzeptanztests, auch an den Engpassdateien aus Regel 8 |
| `agent-antigravity` | Antigravity / Gemini | mittel: mehrere Dateien, Muster, Eskalationsstufe für Qwen |
| `agent-claude` | Claude Code Opus + Mensch | schwer: Betrieb, Server, finale Eskalationsstufe |

Im Zweifel die größere Stufe. Ein Issue, das an einem Agenten scheitert, wird explizit an die
nächsthöhere Stufe eskaliert (`agent-qwen` → `agent-antigravity` → `agent-claude`); das ist keine
Änderung am Vertrag und erlaubt, solange das Issue nicht mehr `in-arbeit` ist.
