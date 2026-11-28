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
([`sandbox/hooks/pre-receive-sperre`](../sandbox/hooks/pre-receive-sperre)), der eingehende
Pushes über den Standard-Transport mit Exit 1 und dieser Zeile beendet:

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
`FREMDER HOOK: …`. Steht der Hook mit richtigem Inhalt, aber ohne Ausführungsrecht da (Git
ignoriert ihn dann), setzt er ihn per `install -m 755` wieder auf 0755 und meldet
`Sperre repariert: …`. `sandbox/install.sh` ruft denselben Installer auf, wenn `$PROJECTS_DIR`
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
git 2.39.5, als `dev` reproduziert: rc=0, Branch angelegt). Ebenso dürfte ein Prozess als `dev`
die Haupt-Repos ohne git beschreiben — Dateien ändern, `.git/hooks` löschen, `.git/refs` direkt
schreiben. Beides schließt erst der eigene Unix-User (nächster Abschnitt); der Hook bleibt die
zweite Barriere.

## Eigener Unix-User `opencode`

Der Lauf läuft als `opencode` (uid 999, gid 995, **nicht** in `dev`). `/config/projects` bleibt
`dev:dev 755/644`: der Lauf kann lesen und klonen, aber nichts schreiben — auch nicht über
`git push --receive-pack`, `.git/config` oder `.git/hooks`.

| Pfad | Besitzer | Modus | Inhalt |
|---|---|---|---|
| `/home/opencode` | opencode:opencode | 700 | HOME des Laufs |
| `/home/opencode/.config/opencode/opencode.json` | opencode:opencode | 600 | Provider, git-Sperre |
| `/home/opencode/.config/opencode/AGENTS.md` | opencode:opencode | 600 | globale Agenten-Regeln (Kopie) |
| `/home/opencode/.local/share/opencode/auth.json` | opencode:opencode | 600 | Anmeldung (Kopie) |
| `/home/opencode/.gitconfig` | opencode:opencode | 600 | `safe.directory = *` — die Haupt-Repos gehören `dev` |
| `/config/work/opencode-runs` | opencode:opencode | 700 | Klone + Patches |
| `/config/logs/opencode-runs` | opencode:opencode | 700 | JSONL-Logs |
| `/opt/opencode/bin/opencode` | root:root | 755 | echte CLI 2.4.2 (Kopie des ELF) |

Der Launcher läuft **selbst** als `opencode`; nur dann gehören Klon, Patch und Log ihm, und
`/config/work/opencode-runs` mit 700 bleibt benutzbar. `git fetch` überspringt das Skript
deshalb (kein Schreibrecht auf `.git/FETCH_HEAD`): der Klon entsteht aus dem lokalen Stand.
Wer frische `origin/main` braucht, fetcht vorher als `dev` im Haupt-Repo.

`dev` darf OpenCode als `opencode` starten, mehr nicht (`/etc/sudoers.d/opencode`):

    dev ALL=(opencode) NOPASSWD: /opt/opencode/bin/opencode

Also `sudo -n -u opencode -H /opt/opencode/bin/opencode …`. Der Launcher braucht die Regel
nicht — er wird direkt als `opencode` gestartet (`runuser -u opencode …`).

`opencode.json` im neuen HOME deklariert dasselbe Plugin `context-mode` wie das Profil von
`dev`; es ist unter `/home/opencode/.cache/opencode/npm/context-mode@latest` abgelegt, und
beim Orca-Probelauf entstand `/home/opencode/.context-mode/projects` — das Plugin läuft also
auch als uid 999. `opencode plugin list` meldet trotzdem „No plugins found"; der Unterbefehl
listet nur anders installierte Plugins, nicht die Deklaration aus der Config.

## Orca startet OpenCode als `opencode` (uid 999)

Orca kennt `opencode`/`opencode2` als eingebaute Agenten und rief bisher das Binary über den
PATH — also als `dev`. Das ist geschlossen: beide Agenten laufen jetzt als uid 999 in einem
Worktree, den `opencode` beschreiben darf.

**Wrapper `/opt/opencode-isolated/opencode`** (root:root 755) — der Dateiname muss mit
`opencode` beginnen, weil Orcas opencode-Parser das Programm am Basename erkennt
(`/^opencode2?(\.(exe|cmd))?$/i`); ein anders benannter Wrapper bekommt weder `--standalone`
noch die erwartete Argumentform.

```sh
#!/bin/sh
for a in "$@"; do
  [ "$a" = "--standalone" ] && exec sudo -Hn -u opencode /opt/opencode/bin/opencode "$@"
done
exec sudo -Hn -u opencode /opt/opencode/bin/opencode --standalone "$@"
```

`--standalone` ist Pflicht: ohne das Flag hängt sich die Instanz an den Hintergrunddienst
des Users und scheitert am festen Port 49374, den der Dienst von `dev` hält
(`Managed service port 49374 on 127.0.0.1 is already in use`). Deshalb genügt die
sudo-Regel aus dem vorigen Abschnitt; ein zweiter Eintrag ist nicht nötig.

**Orca-Override.** `agentCmdOverrides` ist ein Host-Wert in der Settings-Zeile von
`/home/dev/.config/orca/profiles/local-default/profile-state.db`:

    {"opencode": "/opt/opencode-isolated/opencode", "opencode2": "/opt/opencode-isolated/opencode"}

Die Orca-CLI hat keinen Settings-Befehl. Schreiben von außen geht nur bei **gestoppter**
Runtime: `content_hash` ist `sha256(payload)`, die Revision der Zeile `meta.revision + 1`, und
`meta.revision` zieht mit. Läuft `orcad` dabei, bricht es mit
`ProfileStateRevisionConflictError (expected … found …)` ab und schreibt danach keinen
Zustand mehr — also: Runtime stoppen, Zeile schreiben, Runtime starten. Vorher eine Kopie per
`sqlite3.Connection.backup` ziehen (eine Dateikopie verliert das WAL).

**Worktree.** Orcas `workspaceDir` ist global (`/home/dev/orca/workspaces`, `dev:dev`) und
darf nicht auf einen Opencode-Pfad zeigen, sonst träfe es Claude und Antigravity. Statt
dessen existiert ein eigener Klon als Orca-Repo:

| Pfad | Besitzer | Rolle |
|---|---|---|
| `/config/work/orca-opencode/src/agent-sandbox-infra-oc` | opencode:opencode | Klon von `/config/projects/agent-sandbox-infra` (origin = lokaler Pfad), Orca-Repo `agent-sandbox-infra-oc` |
| `/home/dev/orca/workspaces/agent-sandbox-infra-oc/<name>` | dev:dev | von Orca angelegte Worktrees dieses Repos |

Damit `dev` im opencode-eigenen Klon Worktrees anlegen und `opencode` sie beschreiben kann,
tragen Klon und Worktree-Elternverzeichnis ACLs:

```sh
setfacl -R -m u:dev:rwX -m u:opencode:rwX -m d:u:dev:rwX -m d:u:opencode:rwX \
  /config/work/orca-opencode/src/agent-sandbox-infra-oc
setfacl -m u:opencode:rwx -m d:u:opencode:rwx /home/dev/orca/workspaces/agent-sandbox-infra-oc
```

Die Default-Einträge vererben sich auf jedes später angelegte Worktree und auf dessen
gitdir unter `<klon>/.git/worktrees/<name>`. Kein `chmod -R` auf `/home/dev`, keine Änderung
an `/config/projects`. `git` braucht auf beiden Seiten `safe.directory = *`: `dev` in
`/config/.gitconfig` **und** `/home/dev/.gitconfig` (die Runtime startet mit
`HOME=/home/dev`), `opencode` in `/home/opencode/.gitconfig`. Commit-Identität von
`opencode`: `opencode <opencode@claude-desktop.local>`.

Belegt am 28.11.2026 (`orca agent launch --agent opencode --worktree path:<worktree>`):

- Prozess `opencode` (uid 999), cwd = Worktree, Aufruf
  `/opt/opencode/bin/opencode --standalone --prompt …`; Agent schreibt `probe-opencode.txt`,
  `git status --porcelain` zeigt sie, `git commit` als `opencode` läuft durch.
- Lesen aus `/config/projects` funktioniert; `touch /config/projects/home-infra/.git/zz-probe`
  endet mit `Permission denied` (rc=1); in `.git` des Haupt-Repos änderte sich keine Datei,
  die Ref-Liste blieb gleich.
- `opencode2` verhält sich identisch; Claude und Antigravity starten weiter als `dev` im
  eigenen Worktree (`⏵⏵ auto mode on` bzw. `agy`-Prozess mit cwd im Worktree).

Grenzen: nur das Repo `agent-sandbox-infra-oc` ist für uid 999 eingerichtet. Worktrees des
Haupt-Repos (`/opt/claude-desktop/config/projects/…`) bleiben `dev`-Sache — dort kann
`opencode` nicht schreiben, weil das gitdir in `/config/projects` liegt.

## Aufruf (als `opencode`, `HOME=/home/opencode`)

```
runuser -u opencode -- env HOME=/home/opencode \
  python3 /config/projects/agent-sandbox-infra/sandbox/opencode_run.py \
  --repo home-infra --branch docs/opencode-klon-probe --prompt-file /tmp/auftrag.md
```

Optionen: `--model` (Vorgabe `opencode/deepseek-v4.1-flash-free`), `--timeout` (Vorgabe
900 s), `--home` (Vorgabe `/home/opencode`), `--opencode` (Vorgabe
`/opt/opencode/bin/opencode`), `--projects-dir`, `--runs-dir`, `--logs-dir`.

Ablauf:

1. `git fetch` im Haupt-Repo, wenn der Lauf-User dort schreiben darf. Als `opencode` fehlt das
   Recht: das Skript überspringt den Fetch, sagt das und nutzt den lokalen Stand.
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
   ein Aufruf aus einem für den Lauf-User unlesbaren cwd (etwa geerbtes `/root`) scheitert
   sonst mit `Failed to change directory to /root`. `HOME` zeigt auf `/home/opencode`; PATH
   ist `/opt/opencode/bin:/usr/local/bin:/usr/bin:/bin`.
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
zweiter Wachhund. Auf CT 106 ist das der Timer `agy-sessions` als root
(`gc.py --nur-prozesse`, den `prozesse_aufraeumen()` bedient): Klone unter
`/config/work/opencode-runs` werden nach 7 Tagen entfernt, Patches und die JSONL-Logs in
`/config/logs/opencode-runs` nach 30 Tagen. Im Trockenlauf (`GC_PROZESSE_TROCKEN=1`) bleibt
alles stehen. Die Verzeichnisse sind per `OPENCODE_RUNS_DIR` und `OPENCODE_LOGS_DIR`
umstellbar; fehlen sie, passiert nichts.

## Tests

- `tests/test_opencode_run.py`: Befehlsfolge (inkl. `remote remove origin`), Patch-Export,
  Token-Summe, Abbruch bei unbekanntem Repo und bei nicht lesbarem Ref-Schnappschuss,
  Erkennung der pre-receive-Sperre (fehlend, fremder Hook), die Warnung im Lauf sowie
  `HOME`/`PWD` des Modellprozesses und der übersprungene Fetch ohne Schreibrecht
  (ohne echtes Git und ohne Modell).
- `tests/test_pre_receive_sperre.py`: echter Push in ein lokales Haupt-Repo — ohne Hook
  geht er durch, mit Hook werden neuer Branch, Löschung, Tags und Pfad-URL abgewiesen und
  die Refs bleiben unverändert; Installer überschreibt keinen fremden Hook und ist
  wiederholbar.
- `tests/test_gc.py`: 7-Tage-Frist für Klone, 30-Tage-Frist für Belege.
- Echter Lauf auf CT 106: Schadenstest im Klon (inkl. `git push`-Versuch ohne Remote),
  Gegenprobe der Warnung, Ende-zu-Ende mit einem Doku-Auftrag in `home-infra` (Ergebnis
  nur als Patch, kein PR).
