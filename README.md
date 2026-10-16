# 🤖 Agent-Sandbox-Infra

> **Autonome Multi-Agenten-Infrastruktur mit Proxmox LXC, isolierten parallelen Worker-Slots, automatischem Review- und Merge-Takt, LiteLLM-Budgetüberwachung, On-Demand WoL LLM-Host und Home Assistant Live-Telemetrie.**

---

## 🏛️ Architektur-Übersicht

```mermaid
flowchart TD
    subgraph GitHub["GitHub Cloud (Multi-Projekt)"]
        REPO["Repositories (sc-digger, agent-sandbox-infra, ...)<br>• Issues (worker-task, bereit)<br>• PRs & CI (acceptance-guard, pytest, CodeQL)<br>• Multi-Agent Governance & Squash-Merge"]
    end

    subgraph Proxmox["Proxmox VE Server"]
        subgraph CT112["Debian 12 LXC (CT 112: agent-sandbox)"]
            W1["Worker Slot 1 (w1)<br>worker_tick.py"]
            W2["Worker Slot 2 (w2)<br>worker_tick.py"]
            REV["Review-Takt (alle 10 min)<br>review_tick.py (GLM via LiteLLM)"]
            WH["qwen-webhook Service<br>(Live-Log & Slot-Telemetrie)"]
            HS["ha-status Timer (alle 2 min)<br>(Projekt-Matrix & LiteLLM-Budgets)"]
        end

        subgraph LXC113["Debian 12 LXC (CT 113: LiteLLM Proxy)"]
            LLM_PROXY["LiteLLM Proxy (:4000)<br>• coder (DeepSeek V3/V4)<br>• coder-glm (GLM 5.3 Flash)<br>• Fallbacks (coder-or, coder-free)<br>• Virtual Keys & Budget-Tracking"]
        end
    end

    subgraph LLM_Host["Lokaler Gaming-PC (Optionaler LLM Host)"]
        WOL_PROXY["WoL-Proxy (:11434)<br>• Auto-Sleep nach 5 min Idle<br>• On-Demand Wake-on-LAN"]
        OLLAMA["Ollama (:11434)<br>• qwen3-coder-64k (30B MoE, Q4_K_M)<br>• AMD RX 6800 XT (LACT, 2100 MHz, -90 mV)"]
        WOL_PROXY --> OLLAMA
    end

    subgraph SmartHome["Home Assistant (:8123)"]
        HA["Home Assistant OS<br>• Dashboard 'KI Cloud' (system-llm-cloud)<br>• Mushroom Live-Slot-Karten (w1, w2)<br>• Multi-Projekt Matrix & 'Braucht Stephan'-Alerts<br>• Prime-Time Kachel, Budget-Trends & Live-Log"]
    end

    subgraph MusicServer["Musik-Server (LXC 107)"]
        DEPLOY["Auto-Deploy Pull-Timer<br>• sc_digger/autodeploy.py (alle 5-15 min)<br>• ./update.sh (Docker Build & Rauchtest)<br>• Autonomer Rollback bei Fehlschlag"]
    end

    W1 & W2 -->|1. Holt Aufgaben & setzt um| REPO
    W1 & W2 -->|2. Code-Generierung via LiteLLM| LLM_PROXY
    W1 & W2 -.->|Alternativ: Lokales Backend| WOL_PROXY
    W1 & W2 -->|3. Erstellt PRs mit Akzeptanztests| REPO

    REV -->|4. Prüft PRs, urteilt unabhängig| REPO
    REV -->|5. Auto-Merge & Entblocken| REPO
    REV -->|Auswertung via| LLM_PROXY

    WH & HS -->|6. JSON Webhook Telemetrie| HA
    HA -.->|Push bei 'braucht-stephan' / Heartbeat-Ausfall| SmartHome

    REPO -->|7. Auto-Deploy via Read-Only Pull| DEPLOY
```

---

## 🚀 Die 5 Ausbaustufen (Roadmap-Status)

Details und Verträge: [`ROADMAP.md`](ROADMAP.md).

| Stufe | Bezeichnung | Status | Kernmerkmale |
|:---:|---|:---:|---|
| **1** | **Autonomer Auto-Merge & Entblockierung** | ✅ **Live** | `review_tick.py` mergt PRs nach unabhängigem Freigabe-Urteil und grünem CI autonom per Squash-Merge (`gh pr merge --squash`); entblockt Folge-Issues (`Wartet auf: #X, #Y`). |
| **2** | **Strukturierte Slot-Telemetrie & HA-Dashboard** | ✅ **Live** ([#14](https://github.com/tripitest-art/agent-sandbox-infra/issues/14), PR [#48](https://github.com/tripitest-art/agent-sandbox-infra/pull/48)) | Atomare Statusdateien (`slot-1.json`, `slot-2.json`) in `/run/worker-slots/`, PID-Überwachung (Dead-PID Detection), reaktiver Webhook-Stream und Mushroom-Karten mit Live-Task, Modell, Laufzeit-Timer und GitHub-Direktlinks. |
| **3** | **Continuous Deployment (Zero-Touch via `./update.sh`)** | 🔵 **Bereit** ([#15](https://github.com/tripitest-art/agent-sandbox-infra/issues/15)) | Vollständig entkoppelter Pull-Timer auf dem Musik-Server (`sc_digger/autodeploy.py` aus sc-digger #241). Kein SSH aus der Sandbox; automatischer Rollback auf `last_good` bei Build- oder Healthcheck-Fehler. |
| **4** | **Prime-Time & Kosten-intelligentes Scheduling** | ⏳ **Geplant** ([#16](https://github.com/tripitest-art/agent-sandbox-infra/issues/16)) | Dynamisches Umschalten von LiteLLM-Modellen anhand von DeepSeek Prime-Time Tarifen (Peak 1,20 $/1M vs. Off-Peak 0,60 $/1M), automatische Drosselung bei Budgetannäherung. |
| **5** | **Planer-Beschleunigung (AI Issue Pre-Flight)** | ⏳ **Geplant** ([#17](https://github.com/tripitest-art/agent-sandbox-infra/issues/17)) | Automatisierte Akzeptanztest-Validierung (Rot-Prüfung vor Issue-Freigabe), automatische Hotspot- und Abhängigkeitserkennung für den Planer. |

---

## 📁 Repository-Struktur

| Verzeichnis | Beschreibung |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | Roadmap und 5-Stufen-Optimierungsplan für den autonomen Multi-Agenten-Workflow. |
| [`CHANGELOG.md`](CHANGELOG.md) | Was seit dem letzten Deploy neu ist; eine Zeile pro Pull Request. |
| [`AGENTS.md`](AGENTS.md) | Verbindliche Einweisung und Governance-Regeln für alle KI-Agenten (Planer, Worker, Reviewer). |
| [`docs/`](docs/) | Vertiefende Dokumentation: [`planer-ablauf.md`](docs/planer-ablauf.md), [`mcp.md`](docs/mcp.md), [`mehrere-projekte.md`](docs/mehrere-projekte.md), [`backup.md`](docs/backup.md), [`betriebsbericht-2026-10-15.md`](docs/betriebsbericht-2026-10-15.md), GitHub Ruleset [`main-schutz.ruleset.json`](docs/main-schutz.ruleset.json). |
| [`skills/`](skills/) | Agenten-Skills für dieses Projekt (`agent-sandbox-review`). |
| [`.github/`](.github/) | CI (`tests`, `acceptance-guard`), Issue- und PR-Vorlagen. Änderungen hier braucht Stephans Freigabe (`freigabe-geschützt`). |
| [`sandbox/`](sandbox/) | Steuerungslogik: Setup (`install.sh`, `install-lib.sh`), Worker-Takt (`worker_tick.py`), Review-Takt (`review_tick.py`), Multi-Projektverwaltung (`projects.py`), Webhook-Daemon (`qwen_webhook.py`), Status-Reporter (`ha_status.py`) und systemd-Units (`sandbox/systemd/`). |
| [`proxmox/`](proxmox/) | Host-Skripte zur Erstellung des unprivilegierten Debian 12 LXC-Containers auf Proxmox VE (`host-create.sh`). |
| [`hardware/`](hardware/) | AMD RDNA2 GPU-Tuning für den lokalen LLM-Host (LACT, 2100 MHz, −90 mV, 255 W) mit Benchmarks und Ollama-Overrides. |
| [`homeassistant/`](homeassistant/) | YAML-Konfigurationen: REST- und Webhook-Sensoren (`configuration_sensors.yaml`), Lovelace Dashboard-Karten (`dashboard_card.yaml`) und Benachrichtigungs-Automationen. |
| [`tests/`](tests/) | Testsuite für Worker-, Review- und Webhook-Logik, Multi-Projekte und `acceptance_guard.py` (ohne Netzwerk mit Mocks). |

---

## ⚙️ Funktionsweise im Detail

### 1. Parallele Worker-Slots & Regel-8-Schutz
- **Worktree-Isolation:** Mit `MAX_WORKERS=2` arbeitet jeder Slot in einem komplett isolierten Verzeichnis (z. B. `/root/sc-digger-worktrees/w1` und `/root/sc-digger-worktrees/w2`).
- **Dateikollisionsschutz (Regel 8):** Der Taktgeber liest vor Auftragsannahme alle aktiven Aufgaben. Hat ein Issue Überschneidungen in `### Betroffene Dateien` mit einer bereits laufenden Aufgabe oder berührt die Sperrdatei (`hotspot`, z. B. `sc_digger/main.py`), wird es übersprungen, bis der andere Slot fertig ist.
- **Modell-Fallback:** Vor jedem Lauf prüft der Worker mit einem Mini-Probe-Call das primäre Modell (`coder`, DeepSeek). Antwortet es nicht innerhalb des Timeouts, weicht er automatisch auf konfiguierte Alternativen aus (`coder-or` via OpenRouter, `coder-free` via Codestral).

### 2. Autonomer Review-Takt & Auto-Merge
- `review_tick.py` läuft alle 10 Minuten als unabhängiger systemd-Timer.
- **Unabhängiges Modell:** Reviews werden mit `coder-glm` (GLM 5.3 Flash) oder bei großen Diffs mit `longctx-lite` (Gemini 3.5 Flash-Lite) durchgeführt – strikt getrennt von der Worker-Modellfamilie.
- **Automatischer Merge:** Bei Urteil `**Freigegeben**`, bestandenen Pflicht-Checks (`tests`, `acceptance-guard`, `CodeQL`) und `### Merge-Modus: automatisch` führt das Skript den Squash-Merge eigenständig durch.
- **Automatisches Entblocken:** Nach jedem Merge prüft das Skript alle blockierten Issues. Sind alle Bedingungen in `Wartet auf: #X, #Y` erfüllt, wird das Issue automatisch auf `bereit` umgestellt.

### 3. Multi-Projekt-Unterstützung (`/etc/agent-projects.json`)
Die Sandbox betreut mehrere Repositories parallel. In `/etc/agent-projects.json` (Vorlage: [`sandbox/projects.example.json`](sandbox/projects.example.json)) werden Projekte mit Kurzname, GitHub-Repo, lokalem Klon-Pfad und Hotspot-Regeln hinterlegt. Jeder Worker-Slot nimmt die nächste fällige Arbeit aus dem gemeinsamen Pool nach Priorität.

---

## 🚀 Schnelleinrichtung in 4 Schritten

### 1. Sandbox auf Proxmox anlegen
Auf dem Proxmox VE Host:
```bash
CTID=$(pvesh get /cluster/nextid) bash proxmox/host-create.sh
```

### 2. GitHub-Zugang hinterlegen
Im Container CT 112:
- Per `gh auth login --with-token` einen feingranularen GitHub Personal Access Token (PAT) hinterlegen (mit Rechten für Issues, Pull Requests und Workflows im Arbeits-Repository).
- `install.sh` erzeugt bei Bedarf einen lesenden Deploy-Key (`/root/.ssh/agent_infra_deploy`).

### 3. Installation & Konfiguration ausführen
```bash
cd /root/agent-sandbox-infra
bash sandbox/install.sh
```
Das Skript fragt fehlende Geheimnisse verdeckt ab (LiteLLM-Key → `/etc/qwen-worker.env`, Reviewer-Key → `/etc/qwen-reviewer.env`, Webhook-URL → `/etc/qwen-webhook.env`, Status-Webhook-URL → `/etc/ha-status.env`, alle Rechte `0600`) und richtet die systemd-Dienste ein:
- Einstellungen wie `MAX_WORKERS=2`, `REVIEW_POST=1` und `REVIEW_AUTO_MERGE=1` werden dauerhaft in `/etc/agent-sandbox.conf` gespeichert.
- Ressourcenlimits je Worker-Slot: `SLOT_MEMORY_MAX` (z. B. `1200M`), `SLOT_TASKS_MAX` (z. B. `512`) und `SLOT_CPU_PERCENT` (z. B. `150`) landen als `MemoryMax`, `TasksMax` und `CPUQuota` in `qwen-worker@.service` und werden ebenfalls gemerkt. Leer oder `none` heißt kein Limit (so ist es ohne Angabe; ein bestehender Betrieb ändert sich nicht). Ungültige Werte brechen das Skript vor dem Merken ab. Beispiel: `SLOT_MEMORY_MAX=1500M SLOT_TASKS_MAX=256 SLOT_CPU_PERCENT=200 bash sandbox/install.sh`. Gemessen am 16.10.2026 (CT 112, 2 Slots, gut eine Woche Betrieb, aus den cgroup-Dateien von `system-qwen-worker.slice`): Speicherspitze beider Slots zusammen 2704 MiB, davon fast alles Seitencache (aktuell fast kein anonymer Speicher), höchstens 70 Prozesse gleichzeitig, kein Swap. Daraus der Vorschlag für den 4-GB-Container bei 2 Slots: `1500M`, `256`, `200` (Summe 3000M, damit Review, Webhook und System noch Platz haben); bei mehr Slots die Speichergrenze senken. Ein vom Limit beendeter Lauf steht im `journalctl`, nicht als „⚠ Stephan:“-Zeile in `worker.log`.
- Der Timer `ha-status.timer` startet `sandbox/ha_status.py` alle 2 Minuten über einen `ha-status.service` und meldet den Projektstand an die Webhook aus `HA_STATUS_URL` (in `/etc/ha-status.env`). Fehlt die URL, bleibt der Timer aus; Worker- und Review-Takt laufen davon unabhängig.

### 4. Home Assistant anbinden
1. Webhook-IDs in Home Assistant `secrets.yaml` hinterlegen.
2. YAML-Snippets aus [`homeassistant/configuration_sensors.yaml`](homeassistant/configuration_sensors.yaml) in Home Assistant einfügen (`template.reload`).
3. Dashboard-Karten aus [`homeassistant/dashboard_card.yaml`](homeassistant/dashboard_card.yaml) in das Lovelace-Dashboard (z. B. View `system-llm-cloud`) einbinden.

---

## 🛡️ Multi-Agent Governance & Sicherheitsregeln

1. **GitHub `main` ist die einzige Wahrheit:** Agenten arbeiten strikt auf Feature-Branches; Merges erfolgen nur über Pull Requests mit bestandenen Pflicht-Checks (`tests`, `acceptance-guard`, `CodeQL`).
2. **Keine gleichen Familien beim Review:** Wenn ein Modell aus der DeepSeek/Qwen-Familie den Code umsetzt, prüft ein Modell aus der GLM-, Gemini- oder Claude-Familie den Pull Request (und umgekehrt).
3. **Vertragstreue durch den `acceptance-guard`:** Der Akzeptanztest aus dem GitHub-Issue wird zeichengenau als erster Commit gesichert. Der `acceptance-guard` verhindert serverseitig in der CI, dass ein Worker Tests abschwächt, überspringt oder verändert.
4. **Schreibschutz & Sandbox-Isolation:** Der Container ist unprivilegiert und besitzt keinerlei Schreibrechte auf Netzlaufwerke, Hypervisor oder den Musik-Server. Deployments erfolgen ausschließlich entkoppelt per Pull.
5. **Keine Secrets im Repository:** Webhook-IDs, API-Keys und Token liegen ausschließlich in `/etc/*.env` (chmod 600) auf dem Server und erscheinen niemals in Logs, Chat oder Code.
