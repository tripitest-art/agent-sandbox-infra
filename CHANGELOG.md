# Changelog

Was seit dem letzten Deploy neu ist: Stephan liest hier, bevor er auf dem Server
`git pull && bash sandbox/install.sh` ausführt. Format: [Keep a Changelog](https://keepachangelog.com/de/),
neueste Einträge oben. Jede Zeile nennt den Pull Request; die Zeile für einen neuen PR steht
unter „Unreleased“ und kommt aus dem PR-Titel (Regeln: `AGENTS.md`, Abschnitt „Doku und Changelog“).

## Unreleased

### Geändert
- Review-Takt: Pflicht-Checks kommen aus der Projektliste (`required_checks`), Vorgabe `tests` und `acceptance-guard`; sc-digger nennt CodeQL dort selbst (#78).
- Doku-Pflicht: README, ROADMAP und Changelog werden im selben PR wie die Änderung gepflegt;
  ein Test hält die Dateiübersicht aktuell. README und ROADMAP auf den Stand von Stufe 2 gebracht.
- `install.sh`: Der Status-Bericht `ha_status.py` bekommt Service und Timer `ha-status.timer`
  (alle 2 Minuten) und wird über `HA_STATUS_URL` in `/etc/ha-status.env` eingerichtet; ohne die
  URL bleibt der Timer aus, Worker und Review laufen unabhängig (#82).

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
