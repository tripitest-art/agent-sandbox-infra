# Mehrere Projekte: eine Agenten-Infrastruktur für mehrere Repositories

Stand: Oktober 2026. Umgesetzt in den PRs #18 (Projektliste), #19 (`install.sh`) und #20 (Vertrag
im Infra-Repo). Kurzfassung in [`README.md`](../README.md), Regeln in [`AGENTS.md`](../AGENTS.md).

## Wozu

Worker-Takt und Review-Takt waren fest auf `tripitest-art/sc-digger` verdrahtet. Jetzt bedienen sie
eine **Projektliste**. Das zweite Projekt ist diese Infrastruktur selbst
(`tripitest-art/agent-sandbox-infra`); weitere Repositories lassen sich ohne Codeänderung aufnehmen.

## Bausteine

| Teil | Wo | Aufgabe |
|---|---|---|
| Projektliste | `/etc/agent-projects.json` (Vorlage: `sandbox/projects.example.json`) | nennt die Projekte; liegt nie im Repo, weil sie Serverpfade enthält |
| Lademodul | `sandbox/projects.py` | liest und prüft die Liste; ohne Datei gilt nur sc-digger |
| Worker-Takt | `sandbox/worker_tick.py` | `pick_job()` geht die Projekte der Reihe nach durch |
| Review-Takt | `sandbox/review_tick.py` | `run_once()` prüft je Projekt einen PR |
| Einrichtung | `sandbox/install.sh`, `sandbox/install-lib.sh` | klont die Projekte, merkt Einstellungen in `/etc/agent-sandbox.conf` |
| Branch-Schutz | `docs/main-schutz.ruleset.json` | Vorlage des Rulesets (Import in GitHub, siehe unten) |

## Die Projektliste

```json
{"projects": [
  {"name": "sc-digger", "repo": "tripitest-art/sc-digger", "workdir": "/root/sc-digger",
   "test_cmd": "python -m pytest -q", "hotspot": "sc_digger/main.py",
   "hotspot_label": "berührt-main.py", "review_skill": "skills/sc-digger-review/SKILL.md",
   "auto_merge": true, "required_checks": ["tests", "acceptance-guard", "CodeQL"]},
  {"name": "agent-sandbox-infra", "repo": "tripitest-art/agent-sandbox-infra",
   "workdir": "/root/work/agent-sandbox-infra",
   "hotspot": "sandbox/worker_tick.py", "hotspot_label": "berührt-worker_tick.py",
   "review_skill": "skills/agent-sandbox-review/SKILL.md",
   "auto_merge": true,
   "manual_paths": ["AGENTS.md", ".github/*", "tests/acceptance/*", "skills/*",
                    "docs/main-schutz.ruleset.json"]}
]}
```

| Schlüssel | Pflicht | Bedeutung |
|---|:---:|---|
| `name` | ja | Kurzname aus a-z, 0-9, `-`, `_`; steckt in Zustandsdateien und Log-Zeilen |
| `repo` | ja | `owner/name` auf GitHub |
| `workdir` | ja | Klon auf dem Server (absoluter Pfad), Basis der Worktrees `<workdir>-worktrees/w<N>` |
| `test_cmd` | nein | Testbefehl in den Aufträgen an den Worker; Vorgabe `python -m pytest -q` |
| `hotspot` | nein | Datei, an der nie zwei Agenten zugleich arbeiten (Regel 8); ohne Angabe keine solche Sperre |
| `hotspot_label` | nein | Issue-Label für den Hotspot; Vorgabe `berührt-main.py` |
| `review_skill` | nein | Pfad des Review-Skills im Repo; ohne ihn prüft der Reviewer nur nach `AGENTS.md` |
| `auto_merge` | nein | `false`: Der Review-Takt mergt hier nie; Vorgabe `true` |
| `manual_paths` | nein | Dateimuster (`fnmatch`, z. B. `sandbox/*`); ein Treffer lässt den Takt nicht selbst mergen, der PR geht an Stephan. Vorgabe: leer (kein solches Muster) |
| `required_checks` | nein | Checks, die den PR rot machen, wenn sie nicht `SUCCESS` sind; Vorgabe `["tests", "acceptance-guard"]`, sc-digger nennt zusätzlich `CodeQL` |

Die Liste wird streng geprüft: unbekannte Schlüssel, doppelte Namen, Repos oder Ordner, ungültige
Namen und Pfade sowie eine leere oder nicht aus Strings bestehende `required_checks`-Liste führen zu
`ProjectConfigError`. Ebenso wird eine `manual_paths`-Angabe abgewiesen, die keine Liste nicht-leerer
Strings ist: Sonst ließe sich der manuelle Merge still abschalten. Die Takte brechen dann mit
Fehlermeldung ab, damit kein Projekt still wegfällt; `install.sh` bricht ebenso ab.

## Wie die Takte mehrere Projekte behandeln

- **Gemeinsamer Pool, Fair-Share (Least-Recently-Served).** Jeder Worker-Slot nimmt die erste
  Arbeit, beginnend beim Projekt *nach* dem zuletzt bedienten (`_last_project` im Slot-Zustand).
  `ordered_projects()` rotiert die Liste dafür deterministisch; hat das bevorzugte Projekt keine
  fällige Arbeit, folgen die übrigen in derselben Rotation — es gibt also keinen Leerlauf. So
  hungert ein dauerhaft volles erstes Projekt die weiteren nicht mehr aus (#226). Mit nur einem
  Projekt bleibt die Reihenfolge unverändert.
- **Ein Projekt, ein Zustand.** Beim ersten Projekt bleiben alle Pfade und Schlüssel wie vor der
  Projektliste. Ab dem zweiten tragen sie den Namen: Worker-Zustand `infra/pr5@…`,
  Review-Zustand `review-state-<name>.json`, Entwürfe `reviews-<name>/`. PR-Nummern kollidieren so nie.
- **Eigene Worktrees je Projekt:** `<workdir>-worktrees/w<N>`.
- **Fehlender Klon:** wird einmal im Log gemeldet, das Projekt übersprungen.
- **Review:** je Takt höchstens ein PR je Projekt. Ein Fehler in einem Projekt blockiert die anderen
  nicht (Log: `Projekt <name>: Fehler: …`). Regeln, Review-Skill und Pflicht-Checks stammen aus dem
  jeweiligen Repo.
- **`auto_merge: false`:** Auch bei „Merge-Modus automatisch“ im Issue mergt der Takt nicht. Der PR
  geht mit Label `braucht-stephan` und Kommentar an Stephan.
- **`manual_paths`:** Berührt ein freigegebener PR eine Datei, auf die ein Muster passt, mergt der
  Takt nicht selbst; der PR geht mit Label `braucht-stephan` und Kommentar an Stephan. Geprüft wird
  kurz vor dem Merge mit frischer Dateiliste; ist sie nicht lesbar, gilt der PR als betroffen.
  Nur bei „Merge-Modus automatisch“ im Issue kommt es überhaupt so weit.
- **Test-Befehl und Hotspot** stehen im Auftrag an den Worker bzw. steuern die Konfliktprüfung des
  jeweiligen Projekts.

## Takt-Frequenz

Worker: alle `TAKT` (Vorgabe 2 min) je Slot. Review: alle `REVIEW_TAKT` (Vorgabe 2 min). Ein Review
erscheint also etwa 10 Minuten nach grünen Checks, die Nacharbeit läuft binnen 5 Minuten an.

## Einrichten auf dem Server (CT 112)

1. Projektliste anlegen: `/etc/agent-projects.json` (aus `sandbox/projects.example.json`; Pfade prüfen).
2. `cd /root/agent-sandbox-infra && git pull`
3. `MAX_WORKERS=2 REVIEW_POST=1 bash sandbox/install.sh`
   Das Skript klont fehlende Projekte, installiert deren `requirements.txt`, legt Worktree-Ordner an und
   merkt `MAX_WORKERS`, `REVIEW_POST` und `REVIEW_AUTO_MERGE` in `/etc/agent-sandbox.conf`. Spätere Läufe
   übernehmen sie; eine ausdrückliche Angabe gilt und wird neu gemerkt.
4. Prüfen: `tail -n 30 /root/worker.log` und `systemctl list-timers 'qwen-worker*' qwen-review.timer --no-pager`.

**`workdir` darf nie der Klon sein, in dem `git pull` läuft** (`/root/agent-sandbox-infra`): Der Worker
setzt seinen Klon mit `reset --hard` und `clean` zurück. `install.sh` verweigert das.

`install.sh` setzt den Basis-Klon jedes Projekts außerdem auf `origin/main` (abgelöster HEAD, der
alte Branch bleibt bestehen; den Branch `main` belegt der Basis-Klon nicht, weil die Worker-Worktrees ihn
brauchen). Mit lokalen Änderungen oder bei einem Fetch-Fehler bleibt der Klon unberührt, es gibt nur
eine Warnung. Ein Klon auf einem alten Feature-Branch hatte schon rote Tests verursacht.

## Ein neues Projekt aufnehmen

Voraussetzung ist der Vertrag aus sc-digger:

1. **Repo:** `AGENTS.md` (Abschnitte `## Goldene Regeln`, `### Reviewer`), Issue-Formular „Aufgabe für
   einen Agenten“, PR-Vorlage, Workflows `tests` und `acceptance-guard` samt
   `.github/scripts/acceptance_guard.py`. Vorbild: dieses Repo oder sc-digger.
2. **Labels:** `worker-task`, `bereit`, `in-arbeit`, `blockiert`, `braucht-stephan`,
   `freigabe-geschützt`, `phase-N`, `agent-qwen`, `agent-antigravity`, `agent-claude`.
3. **Branch-Schutz:** `docs/main-schutz.ruleset.json` importieren (Settings → Rules → Rulesets → New
   ruleset → Import a ruleset). Der Import setzt `tests` und `acceptance-guard` als Pflicht-Checks voraus,
   die Workflows müssen also schon auf `main` liegen.
4. **Server:** Eintrag in `/etc/agent-projects.json`, dann `bash sandbox/install.sh`.
5. **Entscheidung `auto_merge`:** `false` für alles, was Betrieb, Server oder die Aufsicht selbst betrifft;
   bei gemischten Repositories `true` mit `manual_paths` für die Betriebs- und Aufsichtsdateien.

## Die Infrastruktur als Projekt: Sonderregeln

Die Skripte steuern die Agenten, die sie ändern. Deshalb gilt (siehe `AGENTS.md`, Regeln 4 und 6):

- `auto_merge: true` mit `manual_paths` für die Aufsichtsdateien (`AGENTS.md`, `.github/*`,
  `tests/acceptance/*`, `skills/*`, `docs/main-schutz.ruleset.json`): Der Takt mergt nach „Freigegeben“
  alle PRs ohne Treffer bei „Merge-Modus: automatisch“; Treffer gehen an Stephan. Mit `auto_merge: false`
  prüft und mergt Stephan jeden PR.
- Kein Agent deployt oder führt `install.sh`, `systemctl` oder Befehle auf dem Server aus. Ein Merge
  wirkt erst, wenn Stephan `git pull && bash sandbox/install.sh` ausführt.
- Änderungen, die Review, Modellfamilien-Regel, `auto_merge`, Merge-Bedingungen oder `acceptance-guard`
  lockern, brauchen Stephans ausdrückliche Entscheidung (Label `freigabe-geschützt`).
- Neue Module unter `sandbox/`, die ein Takt-Skript importiert, müssen in `install.sh` mitkopiert werden.

## Bekannte Grenzen und Entscheidungen

- Fair-Share gilt nur zwischen Projekten; die Reihenfolge der Issues *innerhalb* eines Projekts
  ändert das nicht (eigenes Issue).
- Der Review-Takt meldet rote Checks nur für die `required_checks` des Projekts (Vorgabe `tests` und
  `acceptance-guard`, in sc-digger zusätzlich `CodeQL`); er wartet, bis alle vorhandenen Checks fertig
  sind, und verlangt mindestens einen. Ein Projekt ohne `CodeQL` stört nicht.
- `main-schutz` im Infra-Repo kommt ohne CodeQL aus (dort nicht eingerichtet). In sc-digger ist CodeQL
  Pflicht.
- Das Ruleset lässt sich von den Agenten nicht per API anlegen (Schreibzugriff darauf ist gesperrt);
  Stephan importiert es von Hand.
- Die Skripte und `install.sh` wurden mit Fakes getestet; der Lauf auf dem Server ist ein eigener Schritt.

## Tests

`tests/test_projects.py` (Liste, Umschalten, Merge-Sperre), `tests/test_install_lib.py` (gemerkte
Einstellungen, Klonen, Schutz des Deploy-Klons), `tests/test_agents_md.py` (Aufbau und Größe von
`AGENTS.md`), `tests/test_acceptance_guard.py` (aus sc-digger übernommen).
