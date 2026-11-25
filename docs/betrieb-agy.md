# AGY (Antigravity) als zweite Worker- und Review-Familie

Stand 25.11.2026. Ergänzt `AGENTS.md` (Aufsicht und Regeln) um den Betrieb: wann AGY statt Claude
Code oder OpenCode läuft, wie es aufgerufen wird und welche Fallstricke auf CT 106 gelten.

## Wann AGY statt Claude oder OpenCode

AGY läuft über ein eigenes Google-Konto (Google AI Pro) und verbraucht weder
Claude- noch OpenCode-Pensum. Es arbeitet in der Gemini-Familie und ist damit auch als zweite
Review-Stimme brauchbar — aber nie für die eigene Arbeit.

| Aufgabe | AGY geeignet | Warum |
|---|---|---|
| Doku, Changelog, README-Nachträge | ja | Textarbeit, gut prüfbar am Diff |
| Skripte und kleine Werkzeuge mit klarem Auftrag | ja | Auftrag als numerierte Schritte geben |
| klar umrissene UI-Änderungen | ja | Grenze: mehrdeutige Layout-Vorgaben werden geraten |
| Lese-/Audit-Aufgaben (Inventur, Testabdeckung, Log-Auswertung) | ja, bester Fall | Ausgabe in eine Berichtsdatei, kein Codekonflikt |
| Diff-Gegenlesen (andere Familie als der Worker) | ja | siehe „Zweites Review" unten |
| Architekturentscheidungen, mehrdeutige Tickets | nein | dort driften die Gemini-Modelle stärker als Claude |
| Aufgaben mit Push, Deploy oder `install.sh` | nein | Regel 4, und der Worker-User hat keine Push-Rechte |

## Aufruf (nur als `dev`, nur mit `HOME=/config`)

```
runuser -u dev -- env HOME=/config PATH=/config/.local/bin:$PATH \
  sh -c 'cd <verzeichnis> && agy -p "<auftrag>" --output-format json'
```

- Als `root` ohne `HOME=/config` antwortet AGY mit „authentication required" — es findet seine
  OAuth-Daten nicht.
- `-p` ist unbeaufsichtigt; `--output-format json` liefert `{"status":"SUCCESS","response":"…"}`.
  Es gibt **kein** `--max-turns`. `--print-timeout` hat die Vorgabe `0s` und wartet damit bis zum
  Ende des Turns; für unbeaufsichtigte Läufe ein Limit setzen, etwa `--print-timeout 6m`.
- Schreibende Läufe brauchen `--dangerously-skip-permissions`, sonst bleibt AGY an der
  Rechteabfrage stehen. Nur in Wegwerf-Verzeichnissen oder für einen Auftrag, der eine einzelne
  neue Datei anlegt.
- Ausgabe ist Prosa: Zählbares („drei Funktionen ungetestet") selbst nachzählen, nie übernehmen.

## In Orca

```
orca108 agent launch --agent antigravity --worktree "branch:<branch>" --surface terminal
```

- Der Agent heißt **`antigravity`**, nicht `agy` (`Unknown TUI agent "agy"`).
- **Jedes neue Worktree fragt einmal „Do you trust the contents of this project?" und frisst dabei
  den `--prompt`.** Zweistufig vorgehen: `orca108 agent launch …` → Trust-Dialog mit `orca108
  terminal read --terminal <handle> --screen` prüfen → mit `orca108 terminal send --terminal
  <handle> --enter` bestätigen → Prompt danach separat mit `orca108 terminal send --terminal
  <handle> --text "…" --enter` senden.
- Orcas Host-Scope führt keine Antigravity-Konten (`antigravityAccounts: []`), obwohl die CLI
  angemeldet ist. Kontingent deshalb nie aus dem Orca-Panel ableiten, sondern messen.
- **AGY beendet oft ohne Commit**, auch wenn „committen" im Auftrag stand: nach dem Lauf
  `git -C <worktree> status --porcelain` prüfen und den Commit per `terminal send` einfordern.
- Fertige Terminals mit `orca108 terminal close --terminal <handle>` schließen, nicht killen — der
  Verlauf gehört zum Bericht.
- Beim Anlegen des Worktrees legt Orcas Setup-Hook `node_modules/`, `package.json` und
  `pnpm-lock.yaml` ab; die bleiben untracked und gehören in keinen Commit.

## Kontingent messen

```
agy -p "/usage" --output-format json
```

Kostet kein Kontingent und nennt zwei Gruppen mit je einem 5-Stunden- und einem Wochenfenster
(`remaining_fraction`): „Gemini Models" und „Claude and GPT models". Stand 25.11.2026: Gemini
weekly 96 % frei, 5 h 93 % frei, Claude/GPT 100 % frei.

## Prozesshygiene auf CT 106

- **`ps -o etime` ist auf CT 106 defekt** (ein heute gestarteter Prozess zeigt
  `441077229-08:46:56`). Für Altersprüfungen immer `ps -o lstart=` nehmen.
- Ein vergessener AGY-Prozess kostet kein Kontingent, hält aber eine Session und den Speicher.
  Eingesammelt werden sie an genau einer Stelle: `sandbox/gc.py` auf CT 112, das
  `agent-gc.timer` alle 6 Stunden startet. Beendet wird nur `agy` von uid 1000, älter als zwei
  Tage **und** unter 300 s CPU-Zeit oder unter 1 % der Lebenszeit, per SIGTERM mit Protokollzeile
  (PID, Alter, cwd). Ausnahme ist der laufende Review-Eingang unter `/home/dev/agy-review-in/`;
  Trockenlauf über `GC_PROZESSE_TROCKEN=1`. Kein zweiter Wachhund im `nacht-hygiene`-Prompt.

## Zweites Review: `agent-antigravity` (LXC 117)

- Hermes-Cron `agy-review` (alle 20 Minuten, `~/.hermes/profiles/infra/scripts/agy-review.py`,
  Zustand `~/.hermes/profiles/infra/state/agy-review.json`) prüft je Lauf höchstens einen offenen
  PR-Kopf und ist vom GLM-Takt auf CT 112 getrennt.
- Eingabe sind ausschließlich Issue-Text, PR-Text und Diff — keine Kommentare, damit fremder Text
  den Reviewer nicht steuern kann.
- Ergebnis: ein Kommentar am PR plus Commit-Status `agent-antigravity` (`success` = Freigabe,
  `failure` = Änderungen nötig).
- Familienregel: PRs, deren `Worker:`-Zeile Gemini oder Antigravity nennt, prüft der Takt nicht;
  fehlt die Zeile ganz, wird gemeldet statt geprüft.
- Zwei Runden „Änderungen nötig", danach Label `braucht-stephan`.
- Grenze: der Reviewer sieht den Diff, nicht die Volltexte der Dateien. Zeilennummern stammen aus
  dem `@@`-Kopf; bei großen Umbauten ist der GLM-Takt mit vollem Checkout die bessere Instanz.
- Belegt am 25.11.2026 an PR #462 (absichtlich fehlerhafter Platzwächter): AGY benannte den
  eingebauten Fehler mit Datei und Zeile, Runde 2 setzte `braucht-stephan`.
