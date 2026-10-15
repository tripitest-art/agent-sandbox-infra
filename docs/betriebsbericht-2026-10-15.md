# Betriebsbericht 15.10.2026: Zustand der Worker und was daraus folgt

Stand 15.10.2026, Nachmittag. Anlass: Frage nach dem Zustand der Infrastruktur und der Güte der Worker.
Quellen: GitHub (Issues, PRs, Checks), Proxmox-Status, `worker.log` (Auszüge). Keine Zugangsdaten und
keine Adressen in diesem Dokument (Repo ist öffentlich).

## 1. Befund in einem Satz

Die Pipeline läuft stabil und meldet Fehler offen, aber der Durchsatz wird durch drei Dinge begrenzt:
die Regel-8-Sperre auf den Engpassdateien, offene Entscheidungen bei geschützten Akzeptanztests und
einen Fehler in der Arbeitsvergabe (#59).

## 2. Zustand

- **Container CT 112:** läuft seit rund 7 Tagen, CPU etwa 3 %, RAM 0,6 von 4 GB, Disk 4,6 von 21 GB,
  keine Pressure. Letzte Sicherung (vzdump) ohne Fehler.
- **Timer:** `qwen-review.timer` (alle 10 Min.), `qwen-worker@1` und `@2`, Webhook-Dienst aktiv.
- **Bilanz:** 24 PRs gemergt seit dem 11.10., allein am 14.10. etwa 15. Stufe 1 (Auto-Merge,
  Entblocken) und Stufe 2 (Slot-Telemetrie, PR #48) der Roadmap sind fertig. Stufe 3 bis 5 offen.
- **Log:** Von 28 Treffern für „Stephan:“ waren 8 echte Meldungen. Sechs davon waren „Freigegeben,
  Merge-Modus manuell: bitte mergen“ (Normalfall), zwei waren „Reviewer-Konfiguration unbrauchbar“
  (siehe 3.1). Keine Abstürze, kein stilles Scheitern.

## 3. Befunde und Ursachen

### 3.1 Review-Takt blockierte sich selbst (behoben)

`REVIEW_MODEL_BIG=longctx-lite` hatte keine bekannte Modellfamilie. `reviewer_config_problem()` (PR #43)
sperrt solche Konfigurationen bewusst, also gab es keinen Review (Log 14.10., 21:25 und 21:37). Fix:
PR #57, auf dem Server mit `install.sh` aktiv (Anzeige: „Review-Takt: an (…, coder-glm, …)“).
Die README nennt noch `longctx-lite` (Issue #63).

### 3.2 Regel 8 fiel offen aus (behoben)

Zwei Stellen ließen im Fehlerfall „keine Kollision“ zu: `active_in_progress_issues()` schluckte
Ausnahmen, `parse_affected_files()` lieferte bei fehlender Sektion eine leere Menge. Seit #32
(PR #58) gilt „unbekannt = blockieren“. Wirkung im Log nach dem Deploy: Slot w1 überspringt Issues
auf `worker_tick.py`, solange #34 dort arbeitet („berührt … während bereits ein Auftrag … in Arbeit ist“).
Folge: Die Infra-Issues auf `worker_tick.py` laufen nacheinander (#34, #35, #37, #38, #59).

### 3.3 Doppelte Arbeitsvergabe (offen, Issue #59)

Nach dem Review „Änderungen nötig“ von PR #58 (13:04) starteten w2 (13:06) und w1 (13:08) beide
Qwen auf demselben Posten `pr58@13:04:05`. Beide endeten mit „PR-Text geändert, kein neuer Commit“.
Zusätzlich arbeitete w1 um 13:14 noch an dem bereits gemergten PR. Schaden: Rechenzeit und zwei
Textänderungen am PR. Der Worker hat den Fehler selbst als Merkzettel notiert.

### 3.4 Geschützte Akzeptanztests (Entscheidungen)

Gemergte Akzeptanztests und der Issue-Text eines Issues in Arbeit sind unveränderlich
(`acceptance-guard`, Regel 5). Wer sie ändert, braucht `freigabe-geschützt`. Das führte zu drei Fällen:

| Fall | Ursache | Entscheidung 15.10. |
|---|---|---|
| Infra #30 | Akzeptanztest verglich `list(args)[:2]` mit einem Tupel, nie wahr | Eine Zeile im Issue-Block geändert (`seen.append(args)` in `fake_json`), Kommentar im Issue. Der Worker hat daraufhin PR #62 geöffnet |
| Infra #32/#58 | `test_issue_39.py::_issue()` hatte keine Sektion „Betroffene Dateien“, #32 verlangt sie | Eine Zeile in `_issue()` ergänzt (Commit `ab454b1`), PR mit `freigabe-geschützt`, Begründung als PR-Kommentar, gemergt |
| sc-digger #258/#294 | Stephan korrigierte den Issue-Text nach der Übernahme, `acceptance-guard` rot | `freigabe-geschützt`, selbst gemergt |

### 3.5 Review ohne zweite Runde (sc-digger #294)

Das Review lautete „Änderungen nötig“ (Muss: `acceptance-guard` rot). Nach dem Label war der Guard
grün, aber der Kopf-Commit blieb gleich und der Review-Takt prüft pro Commit. Der PR wartete deshalb
auf ein „Freigegeben“, das nicht kam, obwohl Merge-Modus „automatisch“ stand. Der Worker kommentierte
den roten Guard fünfmal ohne Codeänderung. Stephan hat gemergt.

## 4. Betriebsregeln, die sich daraus ergeben

1. **Issue-Text eines Issues in Arbeit nicht ändern.** Geht es nicht anders: Issue zuerst auf `bereit`
   zurücksetzen, solange kein PR existiert (dann zählt die Änderung vor dem Claim), sonst
   `freigabe-geschützt` am PR setzen und die Begründung als PR-Kommentar festhalten.
2. **Vor dem Zurücksetzen auf `bereit` prüfen, ob schon ein PR existiert** (`gh pr list`). Ein
   zurückgesetztes Issue mit offenem PR kann doppelt bearbeitet werden. (Bei #30 am 15.10. kurz
   falsch eingeschätzt und sofort zurückgenommen.)
3. **Ein Review-Urteil gilt nur für seinen Commit.** Wird nach „Änderungen nötig“ nur ein Label gesetzt,
   gibt es kein neues Urteil. Entweder Stephan merged manuell oder es folgt ein neuer Commit.
4. **Deploy erst nach dem Merge:** `cd /root/agent-sandbox-infra && git pull && bash sandbox/install.sh`
   (nur Stephan, Regel 4). Vorher prüfen, ob `git status -sb` „behind“ meldet.
5. **Projektliste auf dem Server** (`/etc/agent-projects.json`) beim Eintrag `agent-sandbox-infra` um
   `hotspot` = `sandbox/worker_tick.py` und `hotspot_label` = `berührt-worker_tick.py` ergänzt
   (Sicherung `.bak`). Das Label existiert im Repo.

## 5. Offene Punkte

| Was | Wer | Stand |
|---|---|---|
| PR #61 (Regel 7, Issue #34) | Review-Takt, dann Stephan | offen |
| PR #62 (Issue #30): `acceptance-guard` rot wegen Issue-Textänderung | Stephan: `freigabe-geschützt` | offen |
| #35, #37, #38, #59 auf `worker_tick.py` | Worker, seriell | in der Warteschlange |
| #31, #46 auf `review_tick.py` | Worker, nach #30 | blockiert durch #30 |
| #54 (`required_checks` pro Projekt) | Worker, wenn die Abhängigkeit aus „Wartet auf:“ erledigt ist | `blockiert` |
| #60 (Tests unabhängig von Servereinstellung) | später | offen, kein `worker-task` |
| #63 (README: Regel 8, Review-Modell) | Doku-Aufgabe | angelegt |
| sc-digger #239/#244 | Stephan: `freigabe-geschützt` entscheiden | offen |
| Stufe 3 bis 5 der Roadmap (#15, #16, #17) | später | offen |

## 6. Nicht geprüft

Die Erfolgsquote pro Slot und Modell über längere Zeit, weil nur Auszüge des Logs vorlagen. Ob
`test_projects.py::test_worker_prompt_uses_project_test_command` nur auf dem Server rot ist, bleibt
im Zusammenhang mit #60 zu klären.
