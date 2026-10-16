# Changelog

Was seit dem letzten Deploy neu ist: Stephan liest hier, bevor er auf dem Server
`git pull && bash sandbox/install.sh` ausführt. Format: [Keep a Changelog](https://keepachangelog.com/de/),
neueste Einträge oben. Jede Zeile nennt den Pull Request; die Zeile für einen neuen PR steht
unter „Unreleased“ und kommt aus dem PR-Titel (Regeln: `AGENTS.md`, Abschnitt „Doku und Changelog“).

## Unreleased

### Hinzugefügt
- `LICENSE` (MIT, Copyright (c) 2026 Stephan Voigt) und `SECURITY.md` mit dem vertraulichen Meldeweg für Sicherheitslücken (GitHub „Private vulnerability reporting“); dazu der README-Abschnitt „Lizenz und Sicherheit“ (#110).
- Web-Dashboard `sandbox/dashboard.py` (nur lesend, SSE) als Ersatz fürs HA-Dashboard (#104).
- `install.sh`: Worker-Takt (`TAKT`) und Review-Takt (`REVIEW_TAKT`) laufen standardmäßig alle 2 Minuten statt alle 5 und 10 (#109).
- `install.sh`: Dienst `agent-dashboard` für das Web-Dashboard, mit `DASHBOARD=1` einschalten, `DASHBOARD_PORT` einstellbar und gemerkt (#105, Teil 2 von #104).
- worker_tick: `pre-push`-Hook im Klon des Slots lehnt den Push bei roten Tests ab; `--no-verify` nur für die Eskalation mit Draft-PR (`ensure_pre_push_hook`, `PUSH_RULE`) (#107).
- HA: Sensor „Agenten Workflow Status“ und Dashboard-Karten für Projekt-Matrix, wartende PRs und LLM-Budgets (Workflow-Status aus `ha_status.py`) (#92).
- Dependabot für die GitHub Actions (wöchentlich, höchstens 2 offene PRs) (#93).
- Doku: Backup und Wiederherstellung der Sandbox (`docs/backup.md`) (#95).
- install.sh: Ressourcenlimits je Worker-Slot (`SLOT_MEMORY_MAX`, `SLOT_TASKS_MAX`, `SLOT_CPU_PERCENT` → `MemoryMax`, `TasksMax`, `CPUQuota`), gemerkt in `/etc/agent-sandbox.conf`; ohne Angabe kein Limit, Vorschlag aus Messung im README (#103, nach #100 und dem Revert #101).

### Geändert
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
- `worker_tick`/`qwen_webhook`: Die Lebendprüfung eines Prozesses nutzt unter Windows die Win32-API (`OpenProcess`/`GetExitCodeProcess`/`CloseHandle`) statt `os.kill(pid, 0)`, das dort `CTRL_C_EVENT` an die ganze Konsole schickt und die pytest-Sitzung auf einem Windows-Rechner abbricht; unter Linux bleibt der Code unverändert (#118).
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
