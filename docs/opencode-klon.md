# OpenCode-Läufe in einem Wegwerf-Klon (CT 106)

OpenCode arbeitet auf der Entwicklungsbox in einem eigenen Klon statt in einem Worktree
des Haupt-Repos. `sandbox/opencode_run.py` legt ihn an, startet den Lauf, exportiert den
Ergebnis-Patch und belegt, dass das Haupt-Repo unberührt blieb.

## Warum

Die git-Sperre in `/config/.config/opencode/opencode.json` ist ein Textmuster auf
Shell-Segmente (`{"*": "allow", "git *": "deny", "*git *": "deny"}`). Sie hält die
naheliegenden Wege dicht — auch `/usr/bin/git`, `env git`, `bash -c`, `xargs git` und
Ketten wie `ls && git status`. Zwei Grenzen bleiben:

- **Nebenwirkung:** `*git *` ist reiner Text. Auch `grep -r "git log" docs/` wird abgewiesen.
- **Restrisiko:** beliebiger Code kommt vorbei. `python3 -c "import subprocess;
  subprocess.run(['git','checkout',…])"` und ein Shell-Skript, das git aufruft, liefen im
  Test durch. Nur ein Verbot von `python3`/`bash` würde das schließen.

Deshalb die zweite Barriere: `git clone --local --no-hardlinks` teilt keine Objekt-Dateien,
Refs und Worktrees mit dem Haupt-Repo. Ein durchgerutschter Befehl (`git branch -D`,
`git reset --hard`, `git worktree remove`, `git update-ref`) trifft nur den eigenen Klon.
Nach dem Anlegen des Branches entfernt das Skript `origin` wieder: sonst zeigt der Remote
als Push-URL aufs Haupt-Repo, und ein `git push` könnte dort fremde Branches und Tags
anlegen oder löschen (nur der ausgecheckte Branch ist durch `receive.denyCurrentBranch`
geschützt).
Die dritte Barriere ist der Ref-Vergleich vor und nach dem Lauf.

Grenze, die auch das Entfernen von `origin` nicht schließt: beliebiger Code kann den Remote
neu setzen — `git remote add x /config/projects/<repo>` oder `git push /config/projects/<repo>`
mit Pfad-URL. Dagegen hilft nur ein Verbot von `bash`/`python3`, das die Karte ausdrücklich
ausschließt. Der Pfad-Rest ist damit Beweis- und Aufräumfunktion, keine Sperre.

## Push-Weg ins Haupt-Repo: zu

Der Push ins Haupt-Repo wird dort abgewiesen, wo er ankommt: Jedes Repo unter
`/config/projects` hat den Hook `.git/hooks/pre-receive`
([`sandbox/hooks/pre-receive-sperre`](../sandbox/hooks/pre-receive-sperre)), der jeden
eingehenden Push mit Exit 1 und dieser Zeile beendet:

    Push in Haupt-Repo auf CT 106 gesperrt (Agenten-Sandbox). Ergebnis als Patch abgeben.

Warum am Empfang und nicht als Kommando-Sperre: Das Kommando (`git *`, `*git *`) ist eine
Textliste mit Lücken — `python3 -c "subprocess.run(['git', …])"` kommt durch, und `.git/config`
im Klon ist beschreibbar. Der Hook sieht jeden Push über den Standard-Transport, gleich mit
welchem Kommando, welcher Pfad-URL oder `file://` er geschickt wird; `git push --receive-pack`
umgeht ihn, weil der Pusher den Empfangsprozess selbst benennt (siehe „Was der Hook nicht
schließt“). Einen Umgehungsschalter über eine Umgebungsvariable
hat er bewusst nicht: die könnte das Modell selbst setzen.

Ausrollen (auf CT 106, als `dev` oder root):

```
bash /config/projects/agent-sandbox-infra/sandbox/hooks/install-sperre.sh
```

Der Installer nimmt alle Git-Repos unter `/config/projects` mit (Vorgabe, per Argument
änderbar), ist beliebig oft lauffähig und erfasst neue Repos bei jedem Lauf. Einen
vorhandenen fremden `pre-receive`-Hook überschreibt er nicht, sondern meldet ihn als
`FREMDER HOOK: …`. `sandbox/install.sh` ruft denselben Installer auf, wenn `$PROJECTS_DIR`
existiert — dieser Lauf gehört Stephan und läuft auf CT 112, wo `/config/projects` fehlt,
dort wird der Installer also übersprungen. Auf CT 106 gibt es keinen Automatismus: den
Befehl oben nach jedem neuen Repo von Hand als `dev` fahren, sonst bleibt das neue Repo ohne
Sperre, und nur die Warnung von `opencode_run.py` erinnert daran.

`opencode_run.py` prüft beim Start, ob die Sperre im Ziel-Repo liegt, und gibt sonst
`WARNUNG: pre-receive-Sperre fehlt in <repo>` aus. Es bricht deswegen nicht ab — ein
fehlender Deploy soll nicht alle Läufe stoppen.

Fetch und Bundle-Import bleiben erlaubt: `git fetch` und `git fetch /tmp/x.bundle main:…`
sind kein Push und laufen am Hook vorbei.

Was der Hook **nicht** schließt: `git push --receive-pack="git -c core.hooksPath=… receive-pack"`
lässt den Pusher den Empfangsprozess selbst benennen, der Hook läuft dann nie (auf CT 106,
git 2.39.5, als `dev` reproduziert: rc=0, Branch angelegt). Ebenso darf der Prozess als `dev`
die Haupt-Repos ohne git beschreiben — Dateien ändern, `.git/hooks` löschen, `.git/refs` direkt
schreiben. Beides ist dieselbe Klasse: dagegen hilft nur ein eigener Unix-Benutzer oder ein
Mount, auf dem `/config/projects` nur lesbar ist. Diese Entscheidung trifft Stephan getrennt;
sie ist nicht Teil dieser Sperre.

## Aufruf (als `dev`, `HOME=/config`)

```
runuser -u dev -- env HOME=/config PATH=/config/.local/bin:/usr/bin:/bin \
  python3 /config/projects/agent-sandbox-infra/sandbox/opencode_run.py \
  --repo home-infra --branch docs/opencode-klon-probe --prompt-file /tmp/auftrag.md
```

Optionen: `--model` (Vorgabe `opencode/deepseek-v4.1-flash-free`), `--timeout` (Vorgabe
900 s), `--projects-dir`, `--runs-dir`, `--logs-dir`, `--opencode` (Vorgabe `$OPENCODE_BIN`
oder `opencode`).

Ablauf:

1. `git fetch` im Haupt-Repo; scheitert er, läuft der Lauf auf dem lokalen Stand weiter und
   sagt das.
2. Ref-Schnappschuss **nach** dem Fetch: `for-each-ref`, `worktree list --porcelain`,
   `stash list`. Ist er nicht lesbar (z. B. `rc=128` bei fremdem Besitzer), bricht das
   Skript mit Exit 2 ab — ein leerer Schnappschuss würde sonst als „unverändert" gelten
   und die Ref-Kontrolle wäre blind.
3. `git clone --local --no-hardlinks /config/projects/<repo> /config/work/opencode-runs/<repo>-<zeitstempel>`,
   dann `git checkout -b <branch> origin/main` und `git remote remove origin` — der Klon
   braucht kein Remote, und `origin` wäre sonst eine Push-URL aufs Haupt-Repo.
4. `opencode run --standalone "<auftrag>" --model … --format json` im Klon, Log nach
   `/config/logs/opencode-runs/<name>.jsonl`. `--standalone` ist Pflicht: ohne das hängt sich
   der Client an den OpenCode-Hintergrunddienst, und dessen cwd (im Test `/config`) gilt
   statt des Klons — das Modell arbeitet dann mit relativen Pfaden im Haupt-Checkout.
   An den Auftrag hängt das Skript zusätzlich einen Satz: „Arbeite ausschließlich im
   aktuellen Arbeitsverzeichnis (Wegwerf-Klon). Das Haupt-Checkout /config/projects/<repo>
   ist tabu — dort nichts ändern. Verwende keine absoluten Pfade außerhalb des Klons."
   Ohne den Satz löst das Modell Pfade aus der Doku zu absoluten Pfaden ins Haupt-Checkout
   auf und ändert dort — der Klon schützt dann nur die Refs, nicht die Dateien.
   Das Skript setzt `PWD` auf den Klon: OpenCode liest sein Projektverzeichnis daraus, und
   ein Aufruf aus einem für `dev` unlesbaren cwd (etwa geerbtes `/root`) scheitert sonst mit
   `Failed to change directory to /root`.
5. `git add -A` (macht das Skript, nicht OpenCode) und
   `git diff --cached --binary > /config/work/opencode-runs/<name>.patch`, dazu `--stat`.
6. Ref-Schnappschuss nachher, Vergleich. Bei Abweichung die Zeile
   `WARNUNG: Haupt-Repo-Refs verändert`, der Diff und Exit 1.
7. Zusammenfassung: Patch, Log, Branch, Laufzeit, Tokens (Summe aus den `step_finish`-Events),
   fehlgeschlagene und von der Sperre abgewiesene Tool-Calls.

Exit-Codes: 0 in Ordnung, 1 Warnung, 2 Aufbaufehler (Repo fehlt, Prompt-Datei nicht lesbar,
Ref-Schnappschuss nicht lesbar, Klon oder Branch scheitert), 3 OpenCode fehlgeschlagen, Zeitlimit
überschritten oder Patch nicht geschrieben. Bei HTTP 429 (Gratis-Limit) nennt die
Zusammenfassung den Reset-Zeitpunkt aus `retryAfterMs` und das Ausweichmodell
`opencode-go/hy4-flash-free` (Gratis-Modell des Go-Endpunkts, belastet das Go-Kontingent nicht).

## Aufräumen

`prune_opencode_runs()` in `sandbox/gc.py` läuft im vorhandenen Housekeeping mit — kein
zweiter Wachhund. Auf CT 106 ist das der Timer `agy-sessions`
(`gc.py --nur-prozesse`, den `prozesse_aufraeumen()` bedient): Klone unter
`/config/work/opencode-runs` werden nach 7 Tagen entfernt, Patches und die JSONL-Logs in
`/config/logs/opencode-runs` nach 30 Tagen. Im Trockenlauf (`GC_PROZESSE_TROCKEN=1`) bleibt
alles stehen. Die Verzeichnisse sind per `OPENCODE_RUNS_DIR` und `OPENCODE_LOGS_DIR`
umstellbar; fehlen sie, passiert nichts.

## Tests

- `tests/test_opencode_run.py`: Befehlsfolge (inkl. `remote remove origin`), Patch-Export,
  Token-Summe, Abbruch bei unbekanntem Repo und bei nicht lesbarem Ref-Schnappschuss,
  Erkennung der pre-receive-Sperre (fehlend, fremder Hook) und die Warnung im Lauf
  (ohne echtes Git und ohne Modell).
- `tests/test_pre_receive_sperre.py`: echter Push in ein lokales Haupt-Repo — ohne Hook
  geht er durch, mit Hook werden neuer Branch, Löschung, Tags und Pfad-URL abgewiesen und
  die Refs bleiben unverändert; Installer überschreibt keinen fremden Hook und ist
  wiederholbar.
- `tests/test_gc.py`: 7-Tage-Frist für Klone, 30-Tage-Frist für Belege.
- Echter Lauf auf CT 106: Schadenstest im Klon (inkl. `git push`-Versuch ohne Remote),
  Gegenprobe der Warnung, Ende-zu-Ende mit einem Doku-Auftrag in `home-infra` (Ergebnis
  nur als Patch, kein PR).
