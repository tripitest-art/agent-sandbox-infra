# Changelog

Was seit dem letzten Deploy neu ist: Stephan liest hier, bevor er auf dem Server
`git pull && bash sandbox/install.sh` ausführt. Format: [Keep a Changelog](https://keepachangelog.com/de/),
neueste Einträge oben. Jede Zeile nennt den Pull Request; die Zeile für einen neuen PR steht
unter „Unreleased“ und kommt aus dem PR-Titel (Regeln: `AGENTS.md`, Abschnitt „Doku und Changelog“).

## Unreleased

### Geändert
- `worker_tick`: Fair-Share-Scheduling über Projekte — `pick_job()` beginnt beim Projekt nach dem zuletzt bedienten (`_last_project` im Slot-Zustand, `ordered_projects()` rotiert die Liste deterministisch) und prüft bei fehlender Arbeit die übrigen in derselben Rotation; ein dauerhaft volles erstes Projekt hungert die weiteren nicht mehr aus, fehlende Klone werden weiter einmal gemeldet und übersprungen (PR folgt).
- `sandbox/core`: GitHub-CLI (`gh`, `gh_json`) und LiteLLM-Proxy (`probe_model`, `get_key_info`) als eigenständige Module mit Unit-Tests; `install.sh` legt sie beim Deploy als `/root/core/` ab (PR folgt).
- `review_tick`: schreibt ein eigenes Audit-Log (`AUDIT_LOG`, Vorgabe `/root/audit.jsonl`) mit JSON-Zeilen für `review_started`, `review_finished` (Urteil, antwortendes Modell, Token-Zahlen, Kosten), `pr_merged` und `to_stephan`; Review-Aktivitäten, Verbrauch und Freigabequote lassen sich damit ohne Log-Regexe auswerten, Geheimnis-Felder stehen nur als `***` drin (PR folgt).
- `worker_tick`: Issue-Kandidaten werden vor der Regel-8-Prüfung nach einem dynamischen Score sortiert — Prioritäts-Labels (`prio:kritisch`/`hoch`/`normal`/`niedrig`), ein Unblock-Hebel je blockierter Folgeaufgabe (`Wartet auf: #N`, höchstens 30 abgefragt, `gh`-Fehler zählen als 0) und ein Altersbonus gegen Verhungern; bei Gleichstand bleibt `sort:created-asc` stabil, und ein unlesbares `createdAt` kostet keinen Bonus (PR folgt).
- `worker_tick`: Issue-Aufträge bekommen Branch und Akzeptanztest-Commit jetzt über `prepare_issue_frame()` aus `worker_frame` (deterministisch, ohne Modell); `autofix_pr_text()` vervollständigt einen unvollständigen PR-Text direkt per `gh pr edit` und fällt bei einem `gh`-Fehler auf `warn_text()` zurück (PR folgt).
- Doku: `CLAUDE.md` (Einzeiler `@AGENTS.md`) und README-Abschnitt „Planer-Umgebung (CT 106)“ mit Grenzen, Ordnern, Werkzeugen und Betrieb; keine Änderung an Code oder Aufsichtsdateien (#241).
- `worker_frame`: neues Modul legt den Ziel-Branch `feature/issue-<N>` an, schreibt den Akzeptanztest-Block aus dem Issue zeichengenau als ersten Commit und baut aus der PR-Vorlage einen vertragskonformen PR-Text; im Takt noch nicht eingehängt (#233).
- `worker_tick`: Verwaiste „in-arbeit“-Claims ohne offenen PR auf `issue_branch(N)` oder lebende Belegung nach `ORPHAN_MINUTEN` (45 min) automatisch freigeben (Label zurück auf `bereit`, `alarm()`- und `audit()`-Meldung; Branches auf GitHub zählen nicht); Dauerkollisionen ab `KOLLISION_MELDUNG_MINUTEN` (60 min) genau einmal als `alarm()` melden (#206).
- `worker_tick`: Die Listenabfragen der offenen PRs (`rework_job`, `text_fix_job`) und Issues (`active_in_progress_issues`, `issue_job`) sind mit `--limit` begrenzt (10 PRs, 20 Issues), damit die verschachtelten Felder das GitHub-GraphQL-Kontingent nicht vorzeitig verbrauchen; die einmalige Abfrage in `open_pr` bleibt ohne Grenze, weil ihr Aufruf in einem gemergten Akzeptanztest zeichengenau geprüft wird (#203).
- `review_tick`: nutzt `--limit` für PR- (`PR_LIST_LIMIT=10`) und Issue-Listen (`BLOCKED_ISSUES_LIMIT=20`), um das GitHub-GraphQL-Kontingent bei jedem Takt zu schonen (#197).
- `install.sh`: merkt sich `TAKT` und `REVIEW_TAKT` in `/etc/agent-sandbox.conf` und liest sie per `conf_get`, damit ein erneuter Lauf (z. B. durch autodeploy) die Timer-Intervalle nicht auf die Vorgabe 2min zurücksetzt (#201).
- `autodeploy`: Pre-Flight-Tests ohne Serverpfade ausführen, Rollback nennt die roten Tests (PR folgt).
- `worker_tick`: Regel 8 übergeht bei der Datei-Kollision die Doku-Pflichtzeilen `CHANGELOG.md`/`README.md` und Akzeptanztest-Platzhalter unter `tests/acceptance/test_issue_*`; echte Code-Kollisionen und der Hotspot blockieren weiter (PR folgt).
- `worker_tick`: Erledigte geparkte Aufträge (PR gemergt oder geschlossen, Issue geschlossen) verlieren ihre Parkmarke `_park:<skey>` samt Versuchszähler, damit die Slot-Telemetrie nicht mehr endlos „wartet auf Stephan“ zeigt; im Leerlauf nennt sie zusätzlich die von Regel 8 übersprungenen Kandidaten mit Grund (PR folgt).
- `install.sh`: fragt im Web-Dashboard-Abschnitt nur noch `DASHBOARD_WRITE_TOKEN` und `NTFY_URL` ab; die Abfrage des `LITELLM_MASTER_KEY` und der Hinweis darauf in der Unit entfallen. Wer den Budget-Weg braucht, trägt den Key von Hand in `/etc/agent-dashboard.env` ein (PR folgt).
- `worker_tick`: Ein Issue, das seit mindestens 40 Minuten auf `in-arbeit` steht, ohne offenen PR auf seinem Branch und ohne lebende Belegung, meldet der erste Slot einmal als „⚠ Stephan:“-Zeile; der Merker `_verwaist:<N>` verschwindet, sobald das Issue nicht mehr verwaist ist, Labels bleiben unberührt (PR folgt).
- Tests: `tests/conftest.py` setzt zusätzlich `AGENT_PROJECTS` auf eine Projektliste mit nicht vorhandenem Klonpfad. `worker_tick.main` setzt `WORKDIR` per `use_project()` aus der Projektliste neu; auf dem Server zeigte das auf einen echten Klon, `test_issue_38/39` waren rot und der Auto-Deploy rollte `609fb8c` zurück (PR folgt).
- Tests: `tests/conftest.py` setzt zusätzlich `AGENT_SETTINGS` auf einen nicht vorhandenen Pfad. Auf dem Server steht in `/etc/agent-settings.json` ein anderes Worker-Modell (`coder-nvidia`); acht Tests (`test_worker_tick.py`, `test_issue_60`) waren dort rot und der Auto-Deploy rollte zurück (PR folgt).
- Tests: `tests/conftest.py` setzt `WORKDIR` auf einen nicht vorhandenen Pfad. Auf dem Server gibt es den echten Worker-Klon; 13 ältere Tests (`test_issue_39/90/106/178`) liefen dort in `prepare_issue_branch` und der Auto-Deploy rollte `56f7582` zurück (PR folgt).
- `dashboard`: Die Kopfzeile zeigt jetzt Kurz-Hash und Zeitpunkt des zuletzt deployten Stands (`.deploy-state.json`, neues Feld `last_good_at` in `autodeploy.py`); ein zurückgerollter Stand erscheint zusätzlich als Banner (#188).
- `worker_tick`: Push und Pull Request legt der Takt nach dem Agentenlauf an; der PR-Text entsteht aus Vorlage und Commit-Nachrichten, ein abgelehnter Push alarmiert (#185).
- `worker_tick`: Branch und Akzeptanztest-Commit legt der Takt vor dem Agentenlauf an; der Auftragstext nennt den fertigen Branch und verlangt kein Kopieren mehr, fehlt der Akzeptanztest-Block im Issue, bricht der Takt mit Alarm ab (#184).
- `worker_tick`: Ein LiteLLM-Ausfall über 20 Minuten erscheint im ersten Slot als einmalige „⚠ Stephan:“-Zeile („LiteLLM seit N Minuten nicht erreichbar; Worker und Review stehen“); die Rückkehr steht nach einem Alarm ohne „⚠“ im Log, ein kurzer Aussetzer bleibt still (#182).
- `worker_tick`/`review_tick`: Das Audit-Log nennt die Modellkosten je Lauf (`run_started`/`run_finished`, `run_cost` aus der Spend-Differenz von `GET /key/info` — bei Überlappung mit einem anderen Slot als Obergrenze markiert) und je gepostetem Review (`review_posted` mit `kosten` aus `usage["cost"]`, Modellliste und Token-Zahlen) (#181).
- `issuecheck`: Der Preflight-Akzeptanztest läuft in `tests/acceptance/` (falls vorhanden), wird auch bei Zeitüberschreitung gelöscht, und ein echter Sammlungsfehler (`ERROR collecting`) ist der Befund `sammlungsfehler` statt „valide und bereit“ (#179).
- `worker_tick`: Räumt zu Beginn jedes Laufs `.done`-Marken und `.claim`-Dateien beendeter Prozesse in `CLAIM_DIR` auf, sobald sie älter als `CLAIM_MAX_AGE_DAYS` (30 Tage) sind; junge Dateien und Belegungen lebender Prozesse bleiben (#177).
- `install.sh`: installiert `logrotate` und legt `/etc/logrotate.d/agent-sandbox` an; `/root/worker.log` und `/root/audit.jsonl` werden ab 50 MB (`LOG_ROTATE_SIZE`) rotiert, vier komprimierte Rotationen, `copytruncate` für die von systemd offen gehaltene Logdatei (#174).
- `worker_tick`: Ist der jüngste abgeschlossene Lauf des Workflows `tests` auf `main` rot, entstehen keine neuen Issue-Aufträge; der Takt meldet das einmal je Commit als `⚠ Stephan:`-Zeile, Nacharbeit und PR-Text laufen weiter (#173).
- `worker_tick`/`review_tick`: Ein gescheiterter `gh`-Aufruf nennt den Grund als festen Klartext (Rate-Limit, Token ungültig, nicht erreichbar), nie den `gh`-Text; der Worker meldet denselben Grund je Slot nur einmal, bis wieder ein Lauf ohne Fehler durchläuft, ein Rate-Limit beendet den Review-Lauf statt die übrigen Projekte abzufragen (#162).
- Doku: Home-Assistant-Rückbau redaktionell abgeschlossen; Architekturdiagramm, README, ROADMAP, autodeploy.md und Review-Skill auf Web-Dashboard und ntfy aktualisiert (#154).
- Home Assistant: Obsolete Module (`ha_status.py`, `qwen_webhook.py`), Konfigurationen (`homeassistant/`), zugehörige Tests und Doku-Links gelöscht (#153).
- `install.sh`: Home-Assistant-Dienste (`qwen-webhook.service`, `ha-status.service`/`timer`) und Secret-Abfragen entfernt (#152).
- `worker_tick`: Das Audit-Log führt zusätzlich `active_model` — mit `BACKEND=litellm` der tatsächlich genutzte Modell-Alias (auch nach dem Ausweichen), sonst wie bisher der Ollama-Name; `model` bleibt unverändert der Ollama-Name (#156).
- Dashboard: von `ha_status` und `qwen_webhook` entkoppelt; `build_status`, Helfer und Slot-Logik direkt im Dashboard implementiert, `ha_status` und `qwen_webhook` reexportieren (#151).
- README (Wake-Listener): `WAKE_URL` ist die JSON-Strom-Adresse `https://ntfy.sh/<Wake-Thema>/json`, getrenntes Thema vom Dashboard, Prüfung per `curl` und Fehlerbild `HTTPError`/`404` (#150).
- README: Regel-8-Dateikollisionsschutz auf tatsächliches Verhalten nach #32 aktualisiert (fail-closed, „unbekannt = blockieren“, Pfaderkennung, Logzeile beim Überspringen), Hotspot-Definition um `sandbox/worker_tick.py` für dieses Repo ergänzt und Review-Modell nach PR #57 auf einheitliches `coder-glm` korrigiert (#145).
- `review_tick`: Scheitert der Commit-Status `review` mit HTTP 401/403/404/422/429, nennt die „⚠ Stephan“-Zeile zusätzlich einen festen Hinweis zur Ursache (bei 403: dem Token fehlt „Commit statuses: write“); nie Text aus dem gh-Fehler (#146).
- `review_tick`: Ein Pflicht-Check, der noch nicht im `statusCheckRollup` steht, gilt bis zehn Minuten nach dem Start des ersten Eintrags als laufend; danach als `FEHLT` und rot. Ein verspätet startender `tests`-Lauf löst so kein falsches „Änderungen nötig“ mehr aus, ohne Zeitstempel gibt es keine Kulanzzeit (#148).
- Dashboard: `--notify-test` liest `/etc/agent-dashboard.env` selbst (kein `set -a` mehr nötig); bei einer Umleitung nennt die Meldung den Zielhost statt nur `HTTP 307` (#143).
- `review_tick`: Ist die Reviewer-Antwort unbrauchbar (Token-Limit, kein Urteil, Zeichensalat, ungültige Muss/Kann-Zahlen), fragt der Takt im selben Lauf das andere der beiden eingestellten Modelle (`REVIEW_MODEL`/`REVIEW_MODEL_BIG`), sofern dessen Familie unabhängig vom Worker ist; erst wenn beide scheitern, zählt das als ein Fehlversuch. Ein Urteil ohne Sterne oder mit Satzzeichen wird ebenfalls gelesen (`normalize_verdict_line`), Reviewer-Zeile und Entwurf nennen das Modell, das wirklich geantwortet hat (#141).
- `review_tick`: PRs ohne Worker-Issue (Label `review-anfordern`) werden nach „Freigegeben“ ebenfalls gemergt, wenn ihr PR-Text „Merge-Modus: automatisch“ sagt (Zeile oder Abschnitt `### Merge-Modus`); `auto_merge: false` und `manual_paths` des Projekts gelten weiter, sonst liegt der PR wie bisher bei Stephan (#138).
- README (Dashboard): Master-Key gehört nicht auf CT 112 (Worker laufen als root), Budgets im LiteLLM-Dashboard ändern; korrigierter Aufruf für `--notify-test` (Umgebungsdatei laden) und Hinweise zu `HTTP 307` und leerem Eingabefeld bei der ntfy-URL (#140).
- `review_tick`: Je Check-Name zählt nur der neueste Lauf (ein von `acceptance-guard` abgebrochener älterer Lauf ist kein rotes Ergebnis mehr), und ein gerade nicht prüfbarer PR blockiert die jüngeren nicht mehr: der Takt überspringt ihn und prüft den nächsten Kandidaten, die Zeile zur fehlenden `Worker:`-Zeile kommt nur einmal je Commit (#137).
- AGENTS.md Regel 4: Merge-Modus „manuell“ bleibt Standard, „automatisch“ nur für PRs ohne Dateien aus `manual_paths` (braucht `freigabe-geschützt`, wirkt mit #122 und Serverkonfiguration) (#123).
- `review_tick`: Kurz vor dem Merge prüft der Takt die frische Dateiliste des freigegebenen PRs gegen die Dateimuster `manual_paths` des Projekts (z. B. `sandbox/*`, `AGENTS.md`); ein Treffer oder eine nicht lesbare Liste geht mit Label `braucht-stephan` an Stephan, ohne Muster bleibt es wie bisher (#126).

### Geändert
- review_tick: Token-Limit der Reviewer-Antwort 16000 statt 6000, einstellbar mit `REVIEW_MAX_TOKENS` (GLM zählt Denk-Tokens mit) (#135).
- Merge-Modus: „automatisch“ ist der Standard für Worker-Issues, manuell nur für Aufsichtsdateien (`manual_paths`); braucht `freigabe-geschützt`, wirkt nach Serverkonfiguration (#133).

### Hinzugefügt
- Dashboard: Abschnitt „Einstellungen“ zeigt die Werte aus `/etc/agent-settings.json` mit Quelle (Datei, Umgebung, Vorgabe) und ändert sie mit dem Schreib-Token über `POST /api/settings`; geschrieben wird nur die Einstellungsdatei, ein Master-Key ist nicht nötig, die Änderung gilt ab dem nächsten Takt (#167).
- Einstellungsdatei: `sandbox/agent_settings.py` liest unkritische Werte (`REVIEW_MAX_TOKENS`, `REVIEW_LLM_TIMEOUT`, `MAX_ROUNDS`, `MAX_TRIES`, `MISSING_CHECK_GRACE_S`, `REVIEW_MODEL_BIG`) aus `/etc/agent-settings.json` (Pfad: `AGENT_SETTINGS`); Datei vor Umgebung vor Vorgabe, ungültige Werte werden ignoriert, Änderungen gelten ab dem nächsten Takt (#166).
- Dashboard: Knopf „Takt starten“ löst Worker- und Review-Takt über den Wake-Listener aus (`POST /api/wake` schickt „wake“ an `WAKE_PUBLISH_URL` in `/etc/agent-dashboard.env`), ohne `systemctl` im Dashboard; fehlt die URL oder der Schreib-Token, erscheint kein Knopf (#163).
- Planer-Preflight: Skript `scripts/issuecheck.py` prüft Issue-Text, Formatierung und Akzeptanztests vor dem Einstellen, führt isolierte pytest-Rot-Läufe durch; neuer Skill `skills/agent-sandbox-planner/SKILL.md` und Dokumentation in `docs/planer-ablauf.md` (Stufe 5, #17).
- `worker_tick`: Audit-Log (`AUDIT_LOG`, Vorgabe `/root/audit.jsonl`) — je Ereignis eine JSON-Zeile mit UTC-Zeitstempel, `event`, Slot, Modell und den übergebenen Feldern (Werte wie `token` oder `api_key` nur als `***`); den Anfang macht die Übernahme eines Issues (`issue_claimed`, Funktion `audit()`), Schreibfehler brechen den Lauf nicht ab (#136).
- `wake_listener`: Dienst `agent-wake` startet Takt-Units sofort bei ntfy-Nachrichten (`WAKE_URL`), periodische Timer bleiben als Sicherheitsnetz (#132).
- Dashboard: Abschnitt „Reviews und Merges“ (Freigabequote, Merges, Übergaben an Stephan, 24 h und 7 Tage, je Projekt und je Reviewer-Modell) aus `worker.log` (`dashboard_stats.py`) (#115).
- Dashboard: Push-Meldungen über ntfy (`dashboard_notify.py`) bei `⚠ Stephan`-Zeilen, totem Slot, Budget ab 90 %, veralteten Daten; `NTFY_URL` in `/etc/agent-dashboard.env`, Test mit `--notify-test` (#114).
- Dashboard: LiteLLM-Budget-Limits (`max_budget`) lassen sich im Browser ändern, geschützt durch einen Schreib-Token und den Master-Key aus `/etc/agent-dashboard.env` (Dienst `agent-dashboard`, `install.sh` fragt beides ab) (#111).
- Auto-Deploy: `sandbox/autodeploy.py` und Timer `agent-sandbox-deploy` holen alle 15 Minuten `origin/main`, testen mit pytest, rollen per `install.sh` aus und setzen bei Fehlern zurück; Einschalten mit `AUTODEPLOY=1 bash sandbox/install.sh` (Betrieb: `docs/autodeploy.md`) (#15).
- `install.sh`: `SKIP_TESTS=1` überspringt den Testlauf von sc-digger am Ende (nur Anzeige, rund 5 Minuten) (#125).
- `LICENSE` (MIT, Copyright (c) 2026 Stephan Voigt) und `SECURITY.md` mit dem vertraulichen Meldeweg für Sicherheitslücken (GitHub „Private vulnerability reporting“); dazu der README-Abschnitt „Lizenz und Sicherheit“ (#110).
- Web-Dashboard `sandbox/dashboard.py` (nur lesend, SSE) als Ersatz fürs HA-Dashboard (#104).
- `install.sh`: Worker-Takt (`TAKT`) und Review-Takt (`REVIEW_TAKT`) laufen standardmäßig alle 2 Minuten statt alle 5 und 10 (#109).
- `install.sh`: Dienst `agent-dashboard` für das Web-Dashboard, mit `DASHBOARD=1` einschalten, `DASHBOARD_PORT` einstellbar und gemerkt (#105, Teil 2 von #104).
- worker_tick: `pre-push`-Hook im Klon des Slots lehnt den Push bei roten Tests ab; `--no-verify` nur für die Eskalation mit Draft-PR (`ensure_pre_push_hook`, `PUSH_RULE`) (#107).
- HA: Sensor „Agenten Workflow Status“ und Dashboard-Karten für Projekt-Matrix, wartende PRs und LLM-Budgets (Workflow-Status aus `ha_status.py`) (#92).
- Dependabot für die GitHub Actions (wöchentlich, höchstens 2 offene PRs) (#93).
- Doku: Backup und Wiederherstellung der Sandbox (`docs/backup.md`) (#95).
- install.sh: Ressourcenlimits je Worker-Slot (`SLOT_MEMORY_MAX`, `SLOT_TASKS_MAX`, `SLOT_CPU_PERCENT` → `MemoryMax`, `TasksMax`, `CPUQuota`), gemerkt in `/etc/agent-sandbox.conf`; ohne Angabe kein Limit, Vorschlag aus Messung im README (#103, nach #100 und dem Revert #101).
- GitHub-Action `.github/workflows/wake.yml`: Weckruf („wake“) an einen ntfy-Strom bei Label `bereit`, PR-Ereignissen und fertigen Checks, damit der Dienst `agent-wake` die Takte sofort startet (Teil 2 von #128, braucht das Secret `WAKE_PUBLISH_URL`) (#129).

### Geändert
- `review_tick`: die Logzeile eines geposteten Reviews nennt den Reviewer („…, als Kommentar-Review gepostet, Reviewer coder-glm (Pfad).“, bei roten Checks ohne Modellaufruf „ohne-Modell“), damit das Dashboard Urteile je Modell zählen kann (#119).
- `review_tick`: Ein PR wird auch dann geprüft, wenn sein Branch nicht `feature/issue-<N>` heißt – dafür setzt jemand mit Schreibrecht das Label `review-anfordern` (z. B. an `feature/dashboard-*` von Claude/Antigravity); ohne Worker-Issue prüft der Takt gegen PR-Text und Regeln und merged nie, der PR liegt nach dem Review bei Stephan (#121).
- `review_tick`: Scheitert das Setzen des Commit-Status `review`, nennt die Warnzeile zusätzlich den HTTP-Statuscode aus dem gh-Fehler (z. B. 403 oder 404), damit ein Rechteproblem von einem Netzfehler unterscheidbar ist; der Fehlertext von gh bleibt weiterhin tabu (#117).
- Review-Takt: Pflicht-Checks kommen aus der Projektliste (`required_checks`), Vorgabe `tests` und `acceptance-guard`; sc-digger nennt CodeQL dort selbst (#78).
- `worker_tick`: Geheimnisse gehen nicht mehr an den Agentenprozess und nicht ins Log, der Agent bekommt nur eine Allowlist der Umgebung (#71).
- `worker_tick`: die PR-Vorlage kommt aus `origin/main` statt aus dem Arbeitsbaum (#74).
- `review_tick`: das Review-Ergebnis wird als Commit-Status `review` gesetzt (#75).
- `tests/test_install_lib.py` wird ohne `bash` und `python3` ausgelassen, in CI nie (#70).
- Doku-Pflicht: README, ROADMAP und Changelog werden im selben PR wie die Änderung gepflegt;
  ein Test hält die Dateiübersicht aktuell. README und ROADMAP auf den Stand von Stufe 2 gebracht.
- `install.sh`: Der Status-Bericht `ha_status.py` bekommt Service und Timer `ha-status.timer`
  (alle 2 Minuten) und wird über `HA_STATUS_URL` in `/etc/ha-status.env` eingerichtet; ohne die
  URL bleibt der Timer aus, Worker und Review laufen unabhängig (#82).
- `worker_tick`: Nacharbeit und PR-Text-Korrektur werden über ein gemeinsames Verzeichnis für alle
  Slots belegt (`CLAIM_DIR`, Vorgabe `/var/lib/worker-claims`); ein belegter Auftrag verbraucht
  keinen Versuch, und eine angekommene Nacharbeit zählt für alle Slots (#94).
- `worker_tick`: die Schlusszeile eines Qwen-Code-Laufs nennt den Token-Verbrauch aus dem
  `result`-Ereignis (`input_tokens`, `output_tokens`, `cache_read_input_tokens`); fehlt die Angabe,
  bleibt die Zeile wie bisher (#99).

### Behoben
- `worker_tick`/`qwen_webhook`: Die Lebendprüfung eines Prozesses nutzt unter Windows die Win32-API (`OpenProcess`/`GetExitCodeProcess`/`CloseHandle`) statt `os.kill(pid, 0)`, das dort `CTRL_C_EVENT` an die ganze Konsole schickt und die pytest-Sitzung auf einem Windows-Rechner abbricht; unter Linux bleibt der Code unverändert (#120).
- `worker_tick`: Ist das Tagesbudget des Worker-Keys aufgebraucht (`spend >= max_budget` aus `/key/info`), wartet der Takt bis zum Reset und verbraucht dabei keinen Versuch; gemeldet wird das einmal je Reset-Zeitraum, ohne Budgetwerte und ohne Entparken (`key_info`, `budget_problem`) (#113).
- `worker_tick`: Scheitert ein Agentenlauf schon am Anbieter (LiteLLM 429/401/5xx), verbraucht er keinen Versuch mehr; der Grund steht gekürzt und ohne Schlüsselteile im Log, und es gibt genau einen „⚠ Stephan“-Alarm je Schlüssel und Fehlertext (`short_api_error`, `LAST_AGENT_ERROR`) (#106).
- `worker_tick`: Regel 8 zählt Dateien aus einem Ausschlusssatz der Sektion „Betroffene Dateien“ („… bleibt unberührt“, „Keine andere Datei …“) nicht mehr als belegt; fremde Issues, die solche Dateien wirklich ändern, werden nicht mehr als Kollision übersprungen (#108).
- `worker_tick`: der Agentenprozess bekommt `LITELLM_API_KEY` wieder; #71 hatte ihn mit entfernt, und jeder Agentenlauf endete nach 0 Sekunden mit `error_during_execution`. Alle anderen Geheimnisse bleiben draußen, das Log bleibt redigiert (#91).
- `worker_tick`: der Agentenprozess behält den PATH aus `ENV` (Fix zu #71), damit `pytest` aus `/root/venv` gefunden wird (#80).
- `review_tick`: Fremdtext wird entschärft, damit keine gefälschte „⚠ Stephan“-Zeile ins Log und nach Home Assistant kommt (#73).

## 2026-10-15

### Hinzugefügt
- Stufe 2: strukturierte Slot-Telemetrie in Home Assistant (#48).
- `projects.py`: `format_projects` für eine lesbare Projektübersicht (#51).
- Betriebsbericht 15.10.2026 (#64).

### Geändert
- `acceptance-guard`: `__init__.py` im Testbaum zählt als geschützte Infrastruktur (#55).
- `install.sh`: `MAX_WORKERS` wird vor dem Merken geprüft, vorhandene Slot-Timer werden
  abgeschaltet, der Review-Timer bleibt im Fehlerfall an (#56).

### Behoben
- Review-Takt: `longctx-lite` sperrte alle Reviews (#57).
- `worker_tick`: Regel 8 fällt bei unbekannter Lage nicht mehr offen aus, sondern blockiert (#58).

## 2026-10-14

### Hinzugefügt
- Mehrere Projekte: Projektliste für Worker- und Review-Takt (#18), Installation klont die Projekte (#19).
- Agenten-Vertrag für dieses Repo: `AGENTS.md`, CI, `acceptance-guard` (#20).
- `ha_status.py`: Workflow-Status als JSON an einen Home-Assistant-Webhook (#41).
- Planer-Ablauf und MCP-Anleitung im Repo (#50), Review-Skill für dieses Projekt (#53).
- Doku zu mehreren Projekten und Ruleset-Vorlage (#21), Roadmap (#13).

### Geändert
- Review-Takt durchgängig: Entwürfe nachholen, Merge wiederholen, an Stephan melden (#12).
- `review_tick`: Unabhängigkeitsregel blockiert bei unbekannter Familie und prüft das aufgerufene Modell (#43).
- `worker_tick`: Slot-Worktrees detached an `origin/main` (#42); geparkte Aufträge blockieren die Warteschlange nicht mehr (#44).
- `install.sh`/`install-lib.sh`: Basis-Klon jedes Projekts auf `main` halten (#23) und ablösen statt den Branch zu belegen (#25); Einstellungsdatei abgesichert, alle Module mitinstalliert (#45).
- `acceptance-guard`: fehlender Diff gilt als Verstoß (#28).
- README an Multi-Worker, Auto-Merge und Roadmap angepasst (#27).

### Behoben
- `worker_tick`: `gh`-Ausgabe ohne JSON wird verkraftet, der Grund beim Claiming geloggt (#26).

## 2026-10-12

### Hinzugefügt
- Multi-Worker: parallele Slots, Git-Worktrees, Regel-8-Konflikterkennung (#10).
- Stufe 1: Auto-Merge freigegebener PRs und automatisches Entblocken (#11).

## 2026-09-30 bis 2026-10-03 (Aufbau)

### Hinzugefügt
- Worker über LiteLLM (DeepSeek) (#1), Worker-Log per Home-Assistant-Webhook (#3).
- Modell-Probe vor jedem Lauf, Ausweichmodell `coder-free` (#5); `coder-or` als erstes Ausweichmodell, tmux (#7).
- Review-Takt: Worker-PRs automatisch prüfen, zunächst nur als Entwurf (#8).

### Geändert
- Deploy-Key nur zum Lesen, das Repo wird privat (#6).
- Hardware-Werte nach Messung (#2).

### Behoben
- Webhook-ID statt URL akzeptieren, Geheimnis nie ausgeben (#4).
- Review-Takt lehnt Antworten am Token-Limit oder mit Zeichensalat ab (#9).
