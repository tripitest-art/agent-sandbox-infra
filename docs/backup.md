# Backup und Wiederherstellung der Sandbox

Was gesichert sein muss, damit CT 112 nach einem Ausfall oder einer Neuinstallation wieder arbeitet,
und in welcher Reihenfolge man den Zustand zurückholt. Das Repo ist öffentlich (Regel 5): Hier stehen nur
Pfade und Variablennamen, nie Werte, Schlüssel oder IP-Adressen. Sicherungsrelevant ist alles, was nicht
aus `main` wiederherstellbar ist.

## Sichern

**Container.** Der ganze Container CT 112 wird per Proxmox-Backup gesichert (vzdump, Ziel und Rhythmus
gehören Stephan: `<von Stephan zu ergänzen>`). Das deckt alles unten Genannte in einem Zug ab. Die
folgenden Einzeldateien sind das Minimum, falls nur eine Datei verloren geht oder der Container neu
entsteht.

**Konfiguration unter `/etc`** (Modus 600, nur root):

| Pfad | Inhalt |
|---|---|
| `/etc/agent-sandbox.conf` | gemerkte Einstellungen von `install.sh` (`MAX_WORKERS`, `REVIEW_POST`, …); ohne sie fällt `install.sh` auf die Vorgaben zurück |
| `/etc/agent-projects.json` | Projektliste (Name, Repository, Arbeitsordner, Hotspot, `auto_merge`, `required_checks`); fehlt sie, kennt der Takt nur sc-digger |
| `/etc/qwen-worker.env` | `LITELLM_API_KEY` (Worker) |
| `/etc/qwen-reviewer.env` | `LITELLM_REVIEW_KEY` (Review-Takt) |
| `/etc/qwen-webhook.env` | `HA_WEBHOOK_URL` (Live-Log in Home Assistant) |
| `/etc/ha-status.env` | `HA_STATUS_URL` (Status-Bericht an Home Assistant) |

Die Werte stehen im Passwortmanager (Vaultwarden), nicht in einer Sicherung im Klartext außerhalb davon.
`ask_secret` in `install.sh` fragt fehlende Werte beim nächsten Lauf verdeckt ab.

**Zustand unter `/root`:**

- `/root/worker-state-w1.json`, `/root/worker-state-w2.json` (Versuchszähler und erledigte Nacharbeiten je
  Slot). Sie zu verlieren ist unkritisch: Die Zähler beginnen bei 0.
- `/root/worker.log` (Protokoll; nur zur Fehlersuche).
- `/root/.ssh/agent_infra_deploy` (Deploy-Schlüssel, nur lesend; ein neuer wird von `install.sh` erzeugt
  und muss bei GitHub unter Deploy keys eingetragen werden).
- die GitHub-Anmeldung von `gh` (`~/.config/gh/`) und die Agentenkonfiguration (`/root/.qwen/settings.json`,
  `/root/.config/opencode/opencode.json`); beide entstehen bei der Einrichtung neu.

**Nicht sichern:** Klone der Projekte und die Worktrees unter `/root/work` und `/root/sc-digger*`. Sie
kommen aus GitHub (`install.sh` klont sie und hält sie auf `main`), und alles Wertvolle steht dort.

**Nicht in einer Sicherung außerhalb des Passwortmanagers ablegen:** die `*.env`-Dateien, den
Deploy-Schlüssel und die `gh`-Anmeldung im Klartext auf einem fremden Laufwerk.

## Wiederherstellen

Reihenfolge, wenn der Container verloren ist:

1. **Container zurückspielen.** Aus dem Proxmox-Backup (`<von Stephan zu ergänzen>`), oder neu anlegen mit
   `proxmox/host-create.sh` (siehe `proxmox/README.md`). Bei einer Rückspielung ist meist nur Schritt 4 nötig.
2. **Repository holen und `install.sh` ausführen.** Nur Stephan, auf dem Server:
   ```bash
   cd /root/agent-sandbox-infra && git pull && bash sandbox/install.sh
   ```
   Das installiert die Pakete, die Agenten (Qwen Code, OpenCode), die Skripte nach `/root`, die systemd-Units
   und klont alle Projekte aus der Projektliste.
3. **Konfiguration und Geheimnisse einspielen.** `/etc/agent-projects.json` und `/etc/agent-sandbox.conf`
   zurückkopieren (oder neu anlegen, `sandbox/projects.example.json` ist die Vorlage). Die Werte der
   `*.env`-Dateien aus dem Passwortmanager eintragen; fehlt einer, fragt `install.sh` im Terminal danach.
   Modus 600 setzen. Den neuen Deploy-Schlüssel bei GitHub eintragen (ohne Schreibrecht) und `gh auth login`
   ausführen.
4. **`install.sh` erneut ausführen** und die Timer prüfen:
   ```bash
   systemctl list-timers 'qwen-worker*' qwen-review.timer ha-status.timer --no-pager
   ```
   Erwartet sind `qwen-worker@1`, `qwen-worker@2` (bei `MAX_WORKERS=2`), `qwen-review.timer` und
   `ha-status.timer`.
5. **Funktion prüfen.** In `/root/worker.log` darf nach dem nächsten Takt keine Zeile mit „⚠ Stephan:“ oder
   `error_during_execution` stehen. Das Dashboard „KI Cloud“ in Home Assistant zeigt den Slot-Status
   wieder an.

Hier mergt und deployt kein Agent: Die Schritte 2 bis 4 führt nur Stephan aus (AGENTS.md, Regel 4).
