# 🚀 Roadmap & Optimierungsplan: Autonomer Multi-Agenten-Workflow

Dieses Dokument beschreibt die Gesamtarchitektur und Entwicklungs-Roadmap für die autonome Multi-Agenten-Infrastruktur (`agent-sandbox-infra`). Ziel ist eine vollständig entkoppelte, fehlertolerante und transparente Pipeline: von der Issue-Planung über isolierte parallele Worker-Slots bis hin zum automatischen Review, Merge und Server-Deployment.

---

## 0. Strategischer Kurswechsel (23.10.2026)

Das ursprünglich mitgeplante lokale LLM-Host-Konzept (eigener Gaming-PC mit Ollama, On-Demand
Wake-on-LAN-Proxy, GPU-Tuning in [`hardware/`](hardware/)) wird **wegen Ineffizienz nicht mehr
weiterverfolgt**: Anschaffung, Stromverbrauch und Wartung eines eigenen GPU-Hosts stehen in keinem
Verhältnis zum Nutzen gegenüber Cloud-Angeboten. Der Ordner `hardware/` bleibt als Referenz stehen,
wird aber nicht mehr ausgebaut; das Architekturdiagramm in [`README.md`](README.md) zeigt an dessen
Stelle die Agentenharness.

Der Fokus der Weiterentwicklung liegt jetzt auf drei Säulen:

1. **Cloud-Agenten statt eigener Hardware:** Modelle laufen über LiteLLM-Proxy (DeepSeek, OpenRouter
   u. a.) oder direkt über **Free-Tiers und günstige Abos** (z. B. OpenCode statt Pay-per-Token-APIs).
   Die Modell-Reihenfolge bevorzugt bereits kostenfreie/günstige Optionen vor bezahlten (#293).
2. **Agentenharness-Flexibilität:** Die austauschbare Anbindung verschiedener Werkzeuge (Qwen Code,
   OpenCode, Claude Code, Antigravity) an den Worker-Takt ist die zentrale Stellschraube, da sie
   bestimmt, welche Modelle, Abo- und Budgetrahmen sich überhaupt nutzen lassen — siehe Stufe 6 unten.
3. **Weiterer Ausbau und Optimierung der Pipeline selbst:** Scheduling (Fair-Share über Projekte,
   Priorisierung, Anti-Starvation), Fehlertoleranz (Stillstands- und Ausfallerkennung, automatische
   Freigabe verwaister Claims) und Kosten-/Fehlertransparenz (Audit-Log, Dashboard-Statistiken) werden
   laufend verfeinert statt in großen Sprüngen — siehe Abschnitt 3 und Backlog unten.

---

## 1. Übersicht der Wertschöpfungskette

```mermaid
flowchart LR
    A["1. Idee / Backlog<br>(Label idee)"] --> B["2. Issue & Akzeptanztest<br>(Planer)"]
    B --> C["3. Multi-Worker<br>(CT 112 Worktrees)"]
    C --> D["4. CI & Checks<br>(acceptance-guard)"]
    D --> E["5. Review-Takt<br>(GLM via LiteLLM)"]
    E --> F["6. Auto-Merge & Unblock<br>(GitHub main)"]
    F --> G["7. Server-Deploy<br>(autodeploy.py, Timer)"]

    style C fill:#d4edda,stroke:#28a745
    style E fill:#d4edda,stroke:#28a745
    style F fill:#d4edda,stroke:#28a745
    style G fill:#d4edda,stroke:#28a745
```

* **Grün:** Bereits vollständig autonom implementiert und im Live-Einsatz.

---

## 2. Die 5 Optimierungsstufen im Detail

---

### ✅ Stufe 1: Autonomer Auto-Merge & Entblockierungs-Kette (Abgeschlossen)

Umgesetzt in PR [#11](https://github.com/tripitest-art/agent-sandbox-infra/pull/11) und verfeinert in PR [#12](https://github.com/tripitest-art/agent-sandbox-infra/pull/12).

#### Funktionsweise:
1. **Auto-Merge bei grünem Urteil:**
   Sobald `review_tick.py` das Urteil `**Freigegeben**` fällt, alle Pflicht-Checks (`tests`, `acceptance-guard`, `CodeQL`) grün sind und im Issue `### Merge-Modus: automatisch` steht, führt das Skript autonom den Squash-Merge aus:
   ```bash
   gh pr merge <PR> --squash
   ```
2. **Automatisches Entblocken (`Wartet auf: #N`):**
   Direkt nach erfolgreichem Merge sowie bei jedem periodischen Review-Takt prüft das Skript alle offenen Issues mit dem Label `blockiert`. Sind alle in der Zeile `Wartet auf:` genannten Abhängigkeiten (PRs oder Issues) geschlossen oder gemergt, tauscht das Skript automatisch:
   `blockiert` $\rightarrow$ `bereit` (`gh issue edit <N> --add-label bereit --remove-label blockiert`).
3. **Schutzmechanismen & Governance:**
   - Bei `### Merge-Modus: manuell` im Issue erfolgt niemals ein automatischer Merge; der PR verbleibt zur Freigabe bei Stephan.
   - Bei `REVIEW_POST=0` (Entwurfsmodus) wird weder gemergt noch umgelabelt.
   - Steuerung über `REVIEW_AUTO_MERGE=1` (Default: aktiv).

---

### ✅ Stufe 2: Strukturierte Slot-Telemetrie & Web-Dashboard (Abgeschlossen)

Umgesetzt in PR [#48](https://github.com/tripitest-art/agent-sandbox-infra/pull/48) und erweitert in PR [#114](https://github.com/tripitest-art/agent-sandbox-infra/pull/114), [#151](https://github.com/tripitest-art/agent-sandbox-infra/pull/155) und [#158](https://github.com/tripitest-art/agent-sandbox-infra/pull/158).

Ziel war es, für die parallelen Worker-Slots strukturierte Telemetrie, ein autarkes Web-Dashboard sowie zuverlässige Push-Meldungen bereitzustellen.

#### Kernpunkte:
1. **JSON-Payload mit strukturiertem Slot-Status:**
   `worker_tick.py` pflegt atomare Statusdateien (`slot-1.json`, `slot-2.json`) unter `/run/worker-slots/` oder `/root/worker-slots/`. Das Dashboard liest diese aus und zeigt Zustandsänderungen sowie tote Prozesse strukturiert an:
   ```json
   {
     "slots": {
       "1": {
         "status": "running",
         "issue": 220,
         "title": "Live-Dashboard API",
         "model": "coder",
         "agent": "Qwen Code mit DeepSeek",
         "started_at": "17:20:00"
       },
       "2": {
         "status": "idle",
         "message": "Warte auf nächsten Takt"
       }
     }
   }
   ```
2. **Autarkes Web-Dashboard (`dashboard.py`):**
   - Live-Slot-Karten (Task, Modell, Laufzeit, Projekt, Status)
   - Multi-Projekt-Matrix und „Wartet auf Stephan“-Übersicht
   - LiteLLM-Budget-Tracking und Review-Statistiken
3. **Push-Benachrichtigungen via ntfy (`dashboard_notify.py`):**
   - Push bei neuen „⚠ Stephan:“-Zeilen, toten Slot-Prozessen und Budgets ab 90 %

---

### ✅ Stufe 3: Continuous Deployment (Abgeschlossen)

Umgesetzt in [#15](https://github.com/tripitest-art/agent-sandbox-infra/issues/15): Ein Pull-Timer auf CT 112
schließt die Bereitstellungskette nach dem Merge in `main` ohne manuellen Zwischenschritt.

#### Kernpunkte:
1. **Automatischer Deploy-Mechanismus:**
   - Alle 15 Minuten startet `agent-sandbox-deploy.timer` das Skript `sandbox/autodeploy.py` und
     vergleicht `origin/main` mit dem zuletzt ausgerollten Stand.
   - Ein neuer Stand wird vorab getestet (Pre-Flight in einer systemd-Sandbox), dann per
     `bash sandbox/install.sh` ausgerollt; solange Worker oder Review laufen, wartet der Lauf.
2. **Sicherheits-Leitplanken:**
   - Deploy erfolgt ausschließlich nach grünem CI und Squash-Merge auf `main`; der Deploy-Key hat kein
     Schreibrecht.
   - Bei roten Tests oder einem Fehler in `install.sh` wird auf den letzten laufenden Stand
     zurückgerollt und eine „⚠ Stephan:“-Zeile ins Log geschrieben. Der Timer ist aus, bis
     `AUTODEPLOY=1 bash sandbox/install.sh` läuft; Ablauf und Betrieb: [`docs/autodeploy.md`](docs/autodeploy.md).

---

### 🔵 Stufe 4: Prime-Time- & Kosten-intelligentes Scheduling

DeepSeek berechnet zu Spitzenzeiten (Mo–Fr) einen Aufschlag (1,20 \$ statt 0,60 \$ pro 1M Tokens). Gleichzeitig soll das konfigurierte Tagesbudget optimal ausgenutzt werden.

#### Kernpunkte:
1. **Peak-Vermeidung für ressourcenintensive Tasks:**
   - Zu Prime-Time-Fenstern weicht der Worker bevorzugt auf `coder-or` (OpenRouter, ohne Peak-Aufschlag) aus oder pausiert neue umfangreiche Aufgaben bis zum Off-Peak-Fenster.
   - Dringende Korrekturen oder kleine Nacharbeiten laufen unterbrechungsfrei weiter.
2. **Dynamischer Budget-Deckel:**
   - Nähert sich der Tagesverbrauch (`sensor.llm_kosten_worker_heute`) dem Limit (z. B. ab 0,85 \$ bei 1,00 \$ Budget), schaltet der Taktgeber automatisch auf kostenfreie Fallback-Modelle (`coder-free` = Codestral) um oder pausiert bis zum Mitternachts-Reset.

---

### 🔵 Stufe 5: Planer-Beschleunigung (AI Issue Pre-Flight)

Die meiste manuelle Vorbereitungszeit fließt in das Ausformulieren präziser Schnittstellen und Akzeptanztests, die den strengen Vorgaben des `acceptance-guard` genügen.

#### Kernpunkte:
1. **Lokales Pre-Flight Tooling (`scripts/issuecheck.py`):**
   - Prüft vor dem Einstellen eines Issues:
     - Ist der Akzeptanztest auf dem aktuellen `main` tatsächlich rot?
     - Werden ausschließlich erlaubte Module importiert (keine verbotenen Netzwerk- oder Dritt-Libraries)?
     - Läuft der Test ohne Zeit-/Zufallsabhängigkeiten und ohne `skip`/`xfail`?
     - Sind Pflichtüberschriften, Merge-Modus und Pfade vertragskonform?
2. **Planer-Skill & Vorlage:**
   - Skill `skills/agent-sandbox-planner/SKILL.md` für Antigravity / Claude, um aus Stichpunkten direkt vertragskonforme Issue-Texte gemäß `docs/planer-ablauf.md` zu generieren und per Pre-Flight abzusichern.

---

### 🔁 Stufe 6: Agentenharness & Pipeline-Optimierung (laufend)

Seit dem Kurswechsel in Abschnitt 0 ist diese Stufe kein einmaliges Projekt mit festem Enddatum,
sondern eine fortlaufende Optimierungsarbeit an zwei eng verzahnten Themen.

#### Agentenharness (austauschbares Werkzeug je Worker-Slot):
1. **OpenCode als Alternative zu Qwen Code** (#270, #271, #282): `AGENT=opencode` lässt einen Slot
   `opencode run --format json` statt Qwen Code starten; die JSONL-Events werden in dieselben
   lesbaren Logzeilen übersetzt (Fließtext, Werkzeugaufrufe, Kosten, Anbieterfehler), Worker-Prompts
   sind auf Englisch umgestellt (weniger Tokens, zuverlässigere Befolgung bei allen Werkzeugen).
2. **Modell-Reihenfolge nach Kosten** (#293): Primär OpenCode Go (`coder-go`, günstig/kostenfrei),
   dann DeepSeek-API (`coder`), zuletzt OpenRouter (`coder-or`); ein Ausweichen auf ein bezahltes
   Modell wird im Log explizit mit Modell, Anbieter und Guthabenverbrauch sichtbar gemacht.
3. **Dauerhafte Konfiguration** (#270): `AGENT` und `CONTEXT_MODE` werden in `/etc/agent-sandbox.conf`
   gemerkt, damit ein Wechsel des Werkzeugs auch den Auto-Deploy übersteht.
4. **Offen:** weitere Werkzeuge (Antigravity im Worker-Takt, nicht nur in der Planer-Umgebung),
   Harness-übergreifende Kostenvergleiche im Dashboard, automatische Auswahl des günstigsten
   verfügbaren Werkzeugs je Auftrag statt fester Konfiguration pro Slot.

#### Pipeline-Ausbau & -Optimierung:
1. **Scheduling:** Prioritäts-Score mit Anti-Starvation (#240), Fair-Share-Rotation über mehrere
   Projekte (`_last_project`), Regel-8-Kollisionsschutz mit Dauerkollisions-Alarm (#206).
2. **Fehlertoleranz:** Stillstandswächter mit Ursachenangabe (#218, #274), Reviewer-Ausweichen bei
   Modellausfall (#273, #275), automatische Freigabe verwaister Claims (#183, #206, #207).
3. **Kosten- und Fehlertransparenz:** Audit-Log mit Lifecycle-Ereignissen und Kosten je Lauf (#178,
   #181, #222), Dashboard-Statistiken zu Reviews, Merges und Übergaben an Stephan (#115, #286).
4. **Offen:** Budget-Dashboards je Agentenharness/Abo statt nur je LiteLLM-Key, automatische
   Nutzung von Free-Tier-Kontingenten mehrerer Anbieter im Wechsel, feinere Kostenprognose vor
   Auftragsvergabe.

---

## 3. Priorisierungs- und Umsetzungsstatus

| Stufe | Feature | Nutzen | Status |
|---|---|---|:---:|
| **Stufe 1** | Auto-Merge & `Wartet auf:` Auto-Unblock | ⭐⭐⭐⭐⭐ | ✅ **Gemergt (#11, #12)** |
| **Stufe 2** | Strukturierte Slot-Telemetrie & Web-Dashboard | ⭐⭐⭐⭐ | ✅ **Gemergt (#48, #114)** |
| **Stufe 3** | Continuous Deployment (`autodeploy.py`, Pull-Timer auf CT 112) | ⭐⭐⭐⭐ | ✅ **Gemergt (#15)** |
| **Stufe 4** | Prime-Time- & Budget-Scheduling | ⭐⭐⭐ | 🔵 **Geplant** |
| **Stufe 5** | Planer-Beschleunigung & Issue-Preflight | ⭐⭐⭐ | 🟡 **In Umsetzung (#17)** |
| **Stufe 6** | Agentenharness & Pipeline-Optimierung (laufend, kein Enddatum) | ⭐⭐⭐⭐⭐ | 🔁 **Laufend (#270, #271, #282, #293)** |
| **Baustein** | Web-Dashboard (`sandbox/dashboard.py`, Dienst `agent-dashboard`) | ⭐⭐⭐⭐ | ✅ **Gemergt (#104, #105, #114, #286)**; Steuerbuttons, Merge-Statistik und Kosten pro Modell offen |
| **Baustein** | Mehrere Projekte (Projektliste, Infra-Repo als zweites Projekt) | ⭐⭐⭐⭐ | ✅ **Gemergt (#18, #19, #20)**; Inbetriebnahme auf dem Server: siehe [`docs/mehrere-projekte.md`](docs/mehrere-projekte.md) |
| **Eingestellt** | Lokaler LLM-Host (Gaming-PC, Ollama, WoL-Proxy, GPU-Tuning) | – | ⛔ **Nicht mehr verfolgt** (Ineffizienz; siehe Abschnitt 0). `hardware/` bleibt als Referenz. |

---

## 4. Backlog

Das Backlog sind Issues mit Label `idee` (ohne Akzeptanztest, ohne `bereit`), keine Datei: so gibt es
nur eine Quelle, die nicht auseinanderläuft. Offene Ideen: `gh issue list --label idee`. Der Planer
macht daraus ein Issue mit Vertrag (`docs/planer-ablauf.md`). Hier stehen nur die Stufen oben; ändert
sich ein Stand, wird diese Datei im selben PR angepasst.

Aktuelle Schwerpunkte für neue Ideen (Stand 23.10.2026, siehe Abschnitt 0 und Stufe 6): weitere
Agentenharness-Werkzeuge und harness-übergreifende Kostenvergleiche, Ausnutzung mehrerer
Free-Tier-Kontingente im Wechsel, sowie laufende Pipeline-Optimierung (Scheduling, Fehlertoleranz,
Kostentransparenz). Ideen rund um einen eigenen lokalen LLM-Host werden nicht mehr aufgenommen.
