#!/usr/bin/env bash
# Richtet die Worker-Sandbox ein oder bringt sie auf den Stand von main. Läuft IM Container
# (CT 112) als root und darf beliebig oft laufen. Anleitung: README.md.
#
#   bash /root/agent-sandbox-infra/sandbox/install.sh  # einrichten / aktualisieren
#   BACKEND=ollama bash …                              # Qwen3-Coder auf dem Gaming-PC statt LiteLLM
#   MODEL=qwen3-coder-64k OLLAMA=http://… bash …       # andere Werte als die Vorgaben
#   AGENT=opencode bash …                              # OpenCode statt Qwen Code
#   WAKE_PC=1 TAKT=30min bash …                        # PC bei Arbeit wecken, seltener schauen
#   REVIEW_POST=1 bash …                               # Review-Takt postet, mergt, entblockt (bleibt bei späteren Läufen)
#   SKIP_TESTS=1 bash …                                # schneller: ohne den Testlauf von sc-digger am Ende (nur Anzeige)
#
# Der GitHub-Zugang wird hier bewusst NICHT eingerichtet: den Token gibt Stephan selbst
# verdeckt ein (README, Schritt 3). Das Skript prüft nur, ob er da ist.
# Geheimnisse stehen nie im Repo (es ist öffentlich): Der LiteLLM-Key kommt nach
# /etc/qwen-worker.env, die Webhook-URL für das Live-Log nach /etc/qwen-webhook.env (beide 600).
# Fehlen sie, fragt das Skript verdeckt danach (nur im Terminal).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)   # sandbox/ dieses Repos
INFRA_DIR=$(cd "$SCRIPT_DIR/.." && pwd)                    # Deploy-Klon dieser Infrastruktur (git pull)
REPO_DIR=/root/sc-digger                                   # Klon von sc-digger (Tests, requirements)
# shellcheck source=install-lib.sh
. "$SCRIPT_DIR/install-lib.sh"
BACKEND=${BACKEND:-litellm}                     # litellm (DeepSeek über LXC 113) oder ollama
LITELLM=${LITELLM:-http://192.168.0.211:4000}
LITELLM_MODEL=${LITELLM_MODEL:-coder}           # Alias im LiteLLM-Proxy
# Ausweichmodelle, wenn LITELLM_MODEL die Probe vor dem Lauf nicht besteht (worker_tick.py)
# coder-or = dasselbe DeepSeek über OpenRouter (weicht dort auf andere Anbieter aus), dann Codestral
LITELLM_FALLBACKS=${LITELLM_FALLBACKS:-coder-or,coder-free}
OLLAMA=${OLLAMA:-http://192.168.0.210:11434}
MODEL=${MODEL:-qwen3-coder-64k}
AGENT=${AGENT:-qwen-code}                       # oder opencode
TAKT=${TAKT:-2min}                              # so oft sucht der Worker Arbeit (ohne Arbeit kostet ein Takt nur GitHub-Abfragen, kein Modell)
# Wo die Unit-Dateien und die aktivierten Timer liegen. Nur für Tests überschreibbar (dort zeigen
# beide auf ein Ersatzverzeichnis, damit kein Lauf auf /etc/systemd/system zugreift; Regel 4).
UNIT_DIR=${UNIT_DIR:-/etc/systemd/system}
TIMER_DIR=${TIMER_DIR:-/etc/systemd/system/timers.target.wants}
# Ohne Angabe gilt die gemerkte Einstellung (/etc/agent-sandbox.conf, install-lib.sh): Ein erneuter
# Lauf (z. B. nach git pull) soll nicht still auf 1 Worker bzw. REVIEW_POST=0 zurückfallen. Beim
# allerersten Lauf mit der Datei zählen die Timer, die schon laufen.
prev_slots=$(ls "$TIMER_DIR"/qwen-worker@*.timer 2>/dev/null | wc -l || true)
saved_workers=$(conf_get MAX_WORKERS)
MAX_WORKERS=${MAX_WORKERS:-${saved_workers:-$(( prev_slots > 1 ? prev_slots : 1 ))}}   # Parallele Worker-Slots (nur bei BACKEND=litellm)
WORKERS_WANTED=$MAX_WORKERS                     # gemerkt wird der Wunsch, nicht die Ollama-Beschränkung
[ "$BACKEND" = litellm ] || MAX_WORKERS=1       # Ollama auf RX 6800 XT erlaubt nur 1 Worker
# Vor dem Merken prüfen: die Einstellungsdatei lässt Buchstaben zu, und ein gemerktes „zwei“ ließe jeden
# weiteren Lauf am `[ "$MAX_WORKERS" -gt 1 ]` unten scheitern – eine Sackgasse, die nur von Hand in
# $CONF wieder aufgeht. Geprüft wird WORKERS_WANTED, also der Wert, der gemerkt würde; das
# Ollama-Limit eine Zeile darüber betrifft nur die Zahl der laufenden Worker.
if ! [[ $WORKERS_WANTED =~ ^[1-9][0-9]*$ ]]; then
  echo "MAX_WORKERS=$WORKERS_WANTED ist keine Zahl >= 1. Nichts gemerkt; mit MAX_WORKERS=<N> erneut starten oder die Zeile aus $CONF entfernen." >&2
  exit 1
fi
WAKE_PC=${WAKE_PC:-0}                           # 1: Gaming-PC bei Arbeit per WoL wecken
QWEN_CODE_VERSION=${QWEN_CODE_VERSION:-0.24.7}  # fest: ein Update soll bewusst passieren
# Review-Takt (review_tick.py): Entwurf ohne Posten, solange REVIEW_POST=0
prev_post=$(sed -n 's/^Environment=REVIEW_POST=//p' /etc/systemd/system/qwen-review.service 2>/dev/null | tail -1 || true)
REVIEW_POST=${REVIEW_POST:-$(conf_get REVIEW_POST)}
REVIEW_POST=${REVIEW_POST:-${prev_post:-0}}     # 1: Review posten, mergen, entblocken (sonst nur Entwurf)
REVIEW_LABELS=${REVIEW_LABELS:-agent-qwen,agent-antigravity,agent-claude}   # diese Worker-PRs prüft der Takt
REVIEW_AUTO_MERGE=${REVIEW_AUTO_MERGE:-$(conf_get REVIEW_AUTO_MERGE)}
REVIEW_AUTO_MERGE=${REVIEW_AUTO_MERGE:-1}       # 1: Freigegebene PRs mit Modus automatisch squashen
# Web-Dashboard (dashboard.py): nur lesend, ohne Login, gehört ins LAN. Aus, bis DASHBOARD=1 gesetzt ist.
DASHBOARD=${DASHBOARD:-$(conf_get DASHBOARD)}
DASHBOARD=${DASHBOARD:-0}                       # 1: Dienst agent-dashboard einrichten und starten
DASHBOARD_PORT=${DASHBOARD_PORT:-$(conf_get DASHBOARD_PORT)}
DASHBOARD_PORT=${DASHBOARD_PORT:-8099}
if ! [[ $DASHBOARD =~ ^[01]$ ]] || ! [[ $DASHBOARD_PORT =~ ^[1-9][0-9]{0,4}$ ]] || [ "$DASHBOARD_PORT" -gt 65535 ]; then
  echo "DASHBOARD muss 0 oder 1 sein, DASHBOARD_PORT eine Portnummer (1-65535). Nichts gemerkt." >&2
  exit 1
fi
# Auto-Deploy (autodeploy.py): holt main alle 15 Minuten und führt dieses Skript aus. Aus, bis AUTODEPLOY=1 gesetzt ist.
AUTODEPLOY=${AUTODEPLOY:-$(conf_get AUTODEPLOY)}
AUTODEPLOY=${AUTODEPLOY:-0}                     # 1: Timer agent-sandbox-deploy einrichten und starten
if ! [[ $AUTODEPLOY =~ ^[01]$ ]]; then
  echo "AUTODEPLOY muss 0 oder 1 sein. Nichts gemerkt." >&2
  exit 1
fi
REVIEW_TAKT=${REVIEW_TAKT:-2min}                # so oft sucht der Review-Takt einen PR (Modellaufruf nur für einen neuen Commit)
REVIEW_MODEL=${REVIEW_MODEL:-coder-glm}         # LiteLLM-Alias (GLM, andere Familie als der Worker)
REVIEW_MODEL_BIG=${REVIEW_MODEL_BIG:-longctx-lite}   # für sehr große Diffs
REVIEWER_NAME=${REVIEWER_NAME:-GLM}             # steht als „Reviewer: …“ im Review
# Limits je Worker-Slot (install-lib.sh): leer oder none = kein Limit. Erst prüfen, dann merken.
SLOT_MEMORY_MAX=${SLOT_MEMORY_MAX:-$(conf_get SLOT_MEMORY_MAX)}
SLOT_TASKS_MAX=${SLOT_TASKS_MAX:-$(conf_get SLOT_TASKS_MAX)}
SLOT_CPU_PERCENT=${SLOT_CPU_PERCENT:-$(conf_get SLOT_CPU_PERCENT)}
slot_limits_check || { echo "Nichts gemerkt; Einstellung korrigieren oder die Zeile aus $CONF entfernen." >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
# Gleich merken, nicht erst am Ende: Bricht ein späterer Schritt ab, gilt der Wunsch beim nächsten Lauf trotzdem.
( MAX_WORKERS=$WORKERS_WANTED; conf_save MAX_WORKERS REVIEW_POST REVIEW_AUTO_MERGE SLOT_MEMORY_MAX SLOT_TASKS_MAX SLOT_CPU_PERCENT DASHBOARD DASHBOARD_PORT AUTODEPLOY ) \
  && echo "Einstellungen gemerkt ($CONF): MAX_WORKERS=$WORKERS_WANTED REVIEW_POST=$REVIEW_POST REVIEW_AUTO_MERGE=$REVIEW_AUTO_MERGE" \
  || echo "Warnung: Einstellungen konnten nicht gemerkt werden ($CONF)"
# Alle LiteLLM-Aliase, die der Worker nutzen kann (Hauptmodell zuerst, ohne Doppelte)
LITELLM_ALL=$(printf '%s\n' "$LITELLM_MODEL" ${LITELLM_FALLBACKS//,/ } | awk 'NF && !seen[$0]++')

echo "== Pakete"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip ffmpeg \
  libsndfile1 libchromaprint-tools tmux >/dev/null   # tmux: install.sh übersteht geschlossene Konsole
# gh aus dem Paketarchiv von GitHub, nicht von Debian: Debians 2.23 fragt bei `gh pr edit` und
# `gh pr view` noch „Projects (classic)“ ab und bricht mit einem GraphQL-Fehler ab. Qwen hielt
# das für Erfolg; der PR-Text von #103 kam zweimal nicht an.
install -d -m 755 /etc/apt/keyrings
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  > /etc/apt/sources.list.d/github-cli.list
apt-get update -qq
apt-get install -y -qq gh >/dev/null

echo "== Repo und Python-Umgebung"
git config --global user.name >/dev/null || git config --global user.name "Stephan (Sandbox-Agent)"
git config --global user.email >/dev/null || git config --global user.email "tripitest@gmail.com"
# Alle Projekte der Projektliste (/etc/agent-projects.json; ohne Datei nur sc-digger) klonen.
# Eine kaputte Liste bricht das Skript ab, statt ein Projekt still wegzulassen.
ensure_projects
[ -x /root/venv/bin/python ] || python3 -m venv /root/venv
for dir in $(project_workdirs); do
  if [ -f "$dir/requirements.txt" ]; then /root/venv/bin/pip install -q -r "$dir/requirements.txt"; fi
done
/root/venv/bin/pip install -q pytest
grep -q 'source /root/venv/bin/activate' /root/.bashrc || echo 'source /root/venv/bin/activate' >> /root/.bashrc

echo "== Geheimnisse (nur lokal, nie im Repo)"
# ask_secret DATEI VARIABLE TEXT: fehlt VARIABLE in DATEI, verdeckt abfragen (nur im Terminal).
ask_secret() {
  local file=$1 var=$2 text=$3 val
  if [ -f "$file" ] && grep -q "^$var=." "$file"; then echo "$var: vorhanden ($file)"; return 0; fi
  if [ ! -t 0 ]; then echo "$var fehlt in $file (Skript im Terminal starten, dann wird gefragt)"; return 1; fi
  read -rsp "$text: " val; echo
  [ -n "$val" ] || { echo "$var leer – übersprungen"; return 1; }
  ( umask 077; { grep -v "^$var=" "$file" 2>/dev/null || true; printf '%s=%s\n' "$var" "$val"; } > "$file.tmp" )
  mv "$file.tmp" "$file"; chmod 600 "$file"
  echo "$var gespeichert ($file)"
}
secrets_ok=1
if [ "$BACKEND" = litellm ]; then
  ask_secret /etc/qwen-worker.env LITELLM_API_KEY "LiteLLM-Key »worker« (aus Vaultwarden)" || secrets_ok=0
fi
# Nur für das Live-Log im HA-Dashboard; fehlt sie, läuft der Worker trotzdem.
# Es reicht die Webhook-ID; eine volle URL http://<HA>:8123/api/webhook/<ID> geht auch.
ask_secret /etc/qwen-webhook.env HA_WEBHOOK_URL "HA-Webhook-ID (aus Vaultwarden)" || true
# Nur für den Status-Bericht im HA-Dashboard; volle Webhook-URL (http://<HA>:8123/api/webhook/<ID>).
ask_secret /etc/ha-status.env HA_STATUS_URL "HA-Webhook-URL für den Status (aus Vaultwarden)" || true
# Eigener, eingeschränkter LiteLLM-Key nur für den Review-Takt (nur REVIEW_MODEL/REVIEW_MODEL_BIG).
# Fehlt er, bleibt der Review-Takt aus; der Worker läuft trotzdem.
review_key=0
ask_secret /etc/qwen-reviewer.env LITELLM_REVIEW_KEY "LiteLLM-Key »reviewer« (aus Vaultwarden)" && review_key=1 || true
# Nur für sofortigen Taktstart bei GitHub-Ereignissen (wake_listener.py); fehlt sie, laufen die Takte per Timer.
ask_secret /etc/agent-wake.env WAKE_URL "ntfy-URL für den Wake-Listener (aus Vaultwarden)" || true

echo "== OpenCode"
[ -x /root/.opencode/bin/opencode ] || curl -fsSL https://opencode.ai/install | bash
mkdir -p /root/.config/opencode
# Nur im Projektordner schreiben (Standard von OpenCode), kein Web; Befehle ohne Rückfrage,
# weil der Timer ohne Terminal läuft und Rückfragen dort automatisch abgelehnt werden.
cat > /root/.config/opencode/opencode.json <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (Gaming-PC)",
      "options": { "baseURL": "$OLLAMA/v1" },
      "models": { "$MODEL": { "name": "$MODEL" } }
    },
    "litellm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "LiteLLM (LXC 113)",
      "options": { "baseURL": "$LITELLM/v1", "apiKey": "{env:LITELLM_API_KEY}" },
      "models": { $(for m in $LITELLM_ALL; do printf '"%s": { "name": "%s" }, ' "$m" "$m"; done | sed 's/, $//') }
    }
  },
  "model": "$([ "$BACKEND" = litellm ] && echo "litellm/$LITELLM_MODEL" || echo "ollama/$MODEL")",
  "permission": { "edit": "allow", "bash": "allow", "webfetch": "deny" }
}
JSON

echo "== Qwen Code"
# Braucht Node 22; Debian 12 hat nur 18. Qwen3-Coder ist auf das Werkzeug-Format von Qwen Code
# trainiert (github.com/QwenLM/qwen-code); OpenCode bleibt als Ausweichweg (AGENT=opencode).
if ! node --version 2>/dev/null | grep -qE '^v(2[2-9]|[3-9][0-9])\.'; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi
[ "$(qwen --version 2>/dev/null)" = "$QWEN_CODE_VERSION" ] || \
  npm install -g -s "@qwen-code/qwen-code@$QWEN_CODE_VERSION"
mkdir -p /root/.qwen
# Ollama als OpenAI-kompatibler Anbieter. Lange Timeouts: das Modell teilt sich den Gaming-PC.
# Sampling ausdrücklich nach der Model-Card von Qwen3-Coder-30B-A3B-Instruct, damit Qwen Code
# keine eigenen Werte schickt, die die Ollama-Modelldatei überschreiben. repetition_penalty 1.05
# reicht Ollamas OpenAI-Schnittstelle nicht durch; das steht in der Modelldatei (repeat_penalty).
cat > /root/.qwen/settings.json <<JSON
{
  "env": { "OLLAMA_API_KEY": "ollama" },
  "modelProviders": {
    "openai": [
      {
        "id": "$MODEL",
        "name": "$MODEL (Ollama, Gaming-PC)",
        "envKey": "OLLAMA_API_KEY",
        "baseUrl": "$OLLAMA/v1",
        "generationConfig": {
          "timeout": 900000,
          "streamIdleTimeoutMs": 900000,
          "maxRetries": 1,
          "contextWindowSize": 65536,
          "samplingParams": { "temperature": 0.7, "top_p": 0.8, "top_k": 20 }
        }
      },
$(for m in $LITELLM_ALL; do cat <<ENTRY
      {
        "id": "$m",
        "name": "$m (LiteLLM)",
        "envKey": "LITELLM_API_KEY",
        "baseUrl": "$LITELLM/v1",
        "generationConfig": {
          "timeout": 600000,
          "streamIdleTimeoutMs": 300000,
          "maxRetries": 1,
          "contextWindowSize": 131072
        }
      },
ENTRY
done | sed '$ s/},$/}/')
    ]
  },
  "security": { "auth": { "selectedType": "openai" } },
  "model": { "name": "$([ "$BACKEND" = litellm ] && echo "$LITELLM_MODEL" || echo "$MODEL")" },
  "privacy": { "usageStatisticsEnabled": false },
  "telemetry": { "enabled": false }
}
JSON

echo "== Taktgeber"
# Alle Python-Module des Repos in einem Schritt nach /root: worker_tick.py und review_tick.py
# importieren projects.py per sys.path, und eine neue Datei unter sandbox/ (z. B. ha_status.py)
# muss ohne weitere Änderung mitkommen – sonst stirbt der Takt beim Start mit einem Traceback.
install -m 644 "$SCRIPT_DIR"/*.py /root/
chmod 755 /root/worker_tick.py /root/review_tick.py /root/qwen_webhook.py /root/wake_listener.py   # werden direkt gestartet

# Worktree-Ordner je Projekt legt ensure_projects an (bei MAX_WORKERS > 1).

# Instanziierte Template-Unit für Slot-Worker (qwen-worker@1, qwen-worker@2, ...)
cat > /etc/systemd/system/qwen-worker@.service <<UNIT
[Unit]
Description=Qwen-Worker Slot %i (sandbox/worker_tick.py)
[Service]
Type=oneshot
Environment=HOME=/root
EnvironmentFile=-/etc/qwen-worker.env
Environment=BACKEND=$BACKEND
Environment=LITELLM=$LITELLM
Environment=LITELLM_MODEL=$LITELLM_MODEL
Environment=LITELLM_FALLBACKS=$LITELLM_FALLBACKS
Environment=OLLAMA=$OLLAMA
Environment=MODEL=$MODEL
Environment=AGENT=$AGENT
Environment=WAKE_PC=$WAKE_PC
Environment=LANG=C.UTF-8
Environment=QWEN_CODE_SUPPRESS_YOLO_WARNING=1
Environment=WORKER_SLOT=%i
$(slot_limit_lines)
ExecStart=/usr/bin/flock -n /run/qwen-worker-w%i.lock /usr/bin/python3 /root/worker_tick.py
StandardOutput=append:/root/worker.log
StandardError=append:/root/worker.log
UNIT

# Standard-Service für manuelle Starts (Slot 1)
cat > /etc/systemd/system/qwen-worker.service <<UNIT
[Unit]
Description=Qwen-Worker: ein Auftrag pro Takt (sandbox/worker_tick.py)
[Service]
Type=oneshot
Environment=HOME=/root
EnvironmentFile=-/etc/qwen-worker.env
Environment=BACKEND=$BACKEND
Environment=LITELLM=$LITELLM
Environment=LITELLM_MODEL=$LITELLM_MODEL
Environment=LITELLM_FALLBACKS=$LITELLM_FALLBACKS
Environment=OLLAMA=$OLLAMA
Environment=MODEL=$MODEL
Environment=AGENT=$AGENT
Environment=WAKE_PC=$WAKE_PC
Environment=LANG=C.UTF-8
Environment=QWEN_CODE_SUPPRESS_YOLO_WARNING=1
Environment=WORKER_SLOT=1
ExecStart=/usr/bin/flock -n /run/qwen-worker-w1.lock /usr/bin/python3 /root/worker_tick.py
StandardOutput=append:/root/worker.log
StandardError=append:/root/worker.log
UNIT

cat > /etc/systemd/system/qwen-worker.timer <<UNIT
[Unit]
Description=Qwen-Worker alle $TAKT
[Timer]
OnBootSec=2min
OnUnitActiveSec=$TAKT
[Install]
WantedBy=timers.target
UNIT

# Alle im System aktivierten Slot-Timer abschalten. Die Liste kommt aus dem System, nicht aus dem
# festen Bereich 1–5: Nach einem Lauf mit MAX_WORKERS=8 blieben qwen-worker@6.timer bis @8.timer
# aktiv, und es liefen mehr Worker als gewünscht (Regel 8: worker_tick.py ist der Engpass).
disable_slot_timers() {
  local timer
  for timer in $(ls "$TIMER_DIR"/qwen-worker@*.timer 2>/dev/null || true); do
    systemctl disable --now "$(basename "$timer")" >/dev/null 2>&1 || true
  done
}

systemctl daemon-reload
if [ "$MAX_WORKERS" -gt 1 ]; then
  # Bei Multi-Worker Instanz-Timer aktivieren (Slots gestaffelt starten)
  systemctl disable --now qwen-worker.timer >/dev/null 2>&1 || true
  disable_slot_timers   # beim Verkleinern fallen so auch Slots oberhalb von MAX_WORKERS weg
  for i in $(seq 1 "$MAX_WORKERS"); do
    boot_delay=$(( i * 2 ))
    cat > "$UNIT_DIR/qwen-worker@$i.timer" <<TUNIT
[Unit]
Description=Qwen-Worker Slot $i alle $TAKT
[Timer]
OnBootSec=${boot_delay}min
OnUnitActiveSec=$TAKT
[Install]
WantedBy=timers.target
TUNIT
    systemctl enable "qwen-worker@$i.timer" >/dev/null
    systemctl restart "qwen-worker@$i.timer"
  done
  echo "Taktgeber: Multi-Worker mit $MAX_WORKERS Slots aktiv (qwen-worker@1..$MAX_WORKERS.timer)"
else
  disable_slot_timers   # auch hier aus dem System, nicht nur aus dem festen Bereich 1–5
  systemctl enable qwen-worker.timer >/dev/null
  systemctl restart qwen-worker.timer
  echo "Taktgeber: Single-Worker an (alle $TAKT)"
fi

echo "== Review-Takt"
# review_tick.py kam schon im Abschnitt Taktgeber mit (alle sandbox/*.py).
cat > /etc/systemd/system/qwen-review.service <<UNIT
[Unit]
Description=Review-Takt: ein PR pro Takt (sandbox/review_tick.py)
[Service]
Type=oneshot
Environment=HOME=/root
# LITELLM_REVIEW_KEY (eigener Key »reviewer«); fehlt die Datei, tut der Takt nichts.
EnvironmentFile=-/etc/qwen-reviewer.env
Environment=LITELLM=$LITELLM
Environment=REVIEW_MODEL=$REVIEW_MODEL
Environment=REVIEW_MODEL_BIG=$REVIEW_MODEL_BIG
Environment=REVIEWER_NAME=$REVIEWER_NAME
Environment=REVIEW_POST=$REVIEW_POST
Environment=REVIEW_AUTO_MERGE=$REVIEW_AUTO_MERGE
Environment=REVIEW_LABELS=$REVIEW_LABELS
Environment=LANG=C.UTF-8
ExecStart=/usr/bin/flock -n /run/qwen-review.lock /usr/bin/python3 /root/review_tick.py
StandardOutput=append:/root/worker.log
StandardError=append:/root/worker.log
UNIT
cat > /etc/systemd/system/qwen-review.timer <<UNIT
[Unit]
Description=Review-Takt alle $REVIEW_TAKT
[Timer]
OnBootSec=3min
OnUnitActiveSec=$REVIEW_TAKT
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
if [ "$review_key" = 1 ]; then
  systemctl enable qwen-review.timer >/dev/null
  systemctl restart qwen-review.timer
  echo "Review-Takt: an (REVIEW_POST=$REVIEW_POST, AUTO_MERGE=$REVIEW_AUTO_MERGE, $REVIEW_MODEL, alle $REVIEW_TAKT)"
else
  systemctl disable --now qwen-review.timer >/dev/null 2>&1 || true
  echo "Review-Takt: aus (LITELLM_REVIEW_KEY fehlt in /etc/qwen-reviewer.env)"
fi

echo "== Log-Stream nach Home Assistant (Webhook)"
# Früher MQTT: Der Login konnte am Broker alles lesen/schreiben (Zendure, Frigate, Zigbee);
# die ACL der Mosquitto-App greift nicht. Alten Dienst und sein Passwort entfernen.
if [ -e /etc/systemd/system/qwen-mqtt.service ] || [ -e /etc/qwen-mqtt.env ]; then
  systemctl disable --now qwen-mqtt.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/qwen-mqtt.service
  [ -e /etc/qwen-mqtt.env ] && shred -u /etc/qwen-mqtt.env
  echo "qwen-mqtt: entfernt (inkl. /etc/qwen-mqtt.env)"
fi
# qwen_webhook.py kam schon im Abschnitt Taktgeber mit (alle sandbox/*.py).
install -m 644 "$SCRIPT_DIR/systemd/qwen-webhook.service" /etc/systemd/system/qwen-webhook.service
systemctl daemon-reload
if [ -f /etc/qwen-webhook.env ] && grep -q '^HA_WEBHOOK_URL=.' /etc/qwen-webhook.env; then
  systemctl enable qwen-webhook.service >/dev/null; systemctl restart qwen-webhook.service
  echo "qwen-webhook: $(systemctl is-active qwen-webhook.service)"
else
  systemctl disable --now qwen-webhook.service >/dev/null 2>&1 || true
  echo "qwen-webhook: aus (/etc/qwen-webhook.env fehlt)"
fi

echo "== Wake-Listener"
# wake_listener.py kam schon im Abschnitt Taktgeber mit (alle sandbox/*.py).
if [ "$MAX_WORKERS" -gt 1 ]; then
  wake_units=$(seq -f "qwen-worker@%g.service" 1 "$MAX_WORKERS" | tr '\n' ' ' | sed 's/ $//')
else
  wake_units="qwen-worker.service"
fi
if [ "$review_key" = 1 ]; then
  wake_units="$wake_units qwen-review.service"
fi
cat > /etc/systemd/system/agent-wake.service <<UNIT
[Unit]
Description=Wake-Listener (sandbox/wake_listener.py)
After=network-online.target
Wants=network-online.target
[Service]
Environment=HOME=/root
Environment=PYTHONUNBUFFERED=1
Environment=LANG=C.UTF-8
Environment=WAKE_UNITS=$wake_units
EnvironmentFile=/etc/agent-wake.env
ExecStart=/usr/bin/python3 /root/wake_listener.py
Restart=always
RestartSec=5
StandardError=append:/root/worker.log
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
if [ -f /etc/agent-wake.env ] && grep -q '^WAKE_URL=.' /etc/agent-wake.env; then
  systemctl enable agent-wake.service >/dev/null; systemctl restart agent-wake.service
  echo "agent-wake: $(systemctl is-active agent-wake.service)"
else
  systemctl disable --now agent-wake.service >/dev/null 2>&1 || true
  echo "agent-wake: aus (WAKE_URL fehlt in /etc/agent-wake.env)"
fi

echo "== Status-Bericht"
# ha_status.py kam schon im Abschnitt Taktgeber mit (alle sandbox/*.py).
cat > /etc/systemd/system/ha-status.service <<UNIT
[Unit]
Description=Workflow-Status an Home Assistant (sandbox/ha_status.py)
[Service]
Type=oneshot
Environment=HOME=/root
# HA_STATUS_URL (Geheimnis) und optional LITELLM_WORKER_KEY/LITELLM_PLANER_KEY für die Budgets.
EnvironmentFile=/etc/ha-status.env
# LITELLM_REVIEW_KEY für das Budget des Reviewers; fehlt die Datei, entfällt nur dieses Budget.
EnvironmentFile=-/etc/qwen-reviewer.env
Environment=LITELLM=$LITELLM
Environment=LANG=C.UTF-8
ExecStart=/usr/bin/flock -n /run/ha-status.lock /usr/bin/python3 /root/ha_status.py
StandardOutput=append:/root/worker.log
StandardError=append:/root/worker.log
UNIT
cat > /etc/systemd/system/ha-status.timer <<UNIT
[Unit]
Description=Status-Bericht alle 2 Minuten
[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
if [ -f /etc/ha-status.env ] && grep -q '^HA_STATUS_URL=.' /etc/ha-status.env; then
  systemctl enable ha-status.timer >/dev/null; systemctl restart ha-status.timer
  echo "ha-status: $(systemctl is-active ha-status.timer)"
else
  systemctl disable --now ha-status.timer >/dev/null 2>&1 || true
  echo "ha-status: aus (HA_STATUS_URL fehlt in /etc/ha-status.env)"
fi

echo "== Web-Dashboard"
# dashboard.py kam schon im Abschnitt Taktgeber mit (alle sandbox/*.py). Der Dienst liest nur (Slots,
# worker.log, gh, LiteLLM) und hat keinen Login: nie hinter eine Portfreigabe stellen.
cat > /etc/systemd/system/agent-dashboard.service <<UNIT
[Unit]
Description=Web-Dashboard der Agenten-Sandbox (sandbox/dashboard.py)
After=network-online.target
Wants=network-online.target
[Service]
Environment=HOME=/root
Environment=PYTHONUNBUFFERED=1
Environment=LANG=C.UTF-8
Environment=DASHBOARD_PORT=$DASHBOARD_PORT
Environment=LITELLM=$LITELLM
# Budget-Keys wie bei ha-status (LITELLM_WORKER_KEY/LITELLM_PLANER_KEY von Hand in /etc/ha-status.env);
# fehlt eine Datei, entfällt nur das jeweilige Budget.
EnvironmentFile=-/etc/ha-status.env
EnvironmentFile=-/etc/qwen-reviewer.env
NoNewPrivileges=yes
ExecStart=/usr/bin/python3 /root/dashboard.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
if [ "$DASHBOARD" = 1 ]; then
  systemctl enable agent-dashboard.service >/dev/null; systemctl restart agent-dashboard.service
  echo "agent-dashboard: $(systemctl is-active agent-dashboard.service) (Port $DASHBOARD_PORT, nur im LAN)"
else
  systemctl disable --now agent-dashboard.service >/dev/null 2>&1 || true
  echo "agent-dashboard: aus (mit DASHBOARD=1 einschalten)"
fi

echo "== Auto-Deploy"
# autodeploy.py kam schon im Abschnitt Taktgeber mit (alle sandbox/*.py). Nur den Timer starten, nie
# den Dienst: Dieses Skript kann vom Dienst selbst laufen, und ein Neustart würde ihn mittendrin beenden.
install -m 644 "$SCRIPT_DIR/systemd/agent-sandbox-deploy.service" "$UNIT_DIR/agent-sandbox-deploy.service"
install -m 644 "$SCRIPT_DIR/systemd/agent-sandbox-deploy.timer" "$UNIT_DIR/agent-sandbox-deploy.timer"
systemctl daemon-reload
if [ "$AUTODEPLOY" = 1 ]; then
  systemctl enable agent-sandbox-deploy.timer >/dev/null; systemctl restart agent-sandbox-deploy.timer
  echo "agent-sandbox-deploy: $(systemctl is-active agent-sandbox-deploy.timer) (alle 15 Minuten, siehe docs/autodeploy.md)"
else
  systemctl disable --now agent-sandbox-deploy.timer >/dev/null 2>&1 || true
  echo "agent-sandbox-deploy: aus (mit AUTODEPLOY=1 einschalten)"
fi

echo "== Infra-Repo: Deploy-Key (nur lesen)"
# Das Repo ist privat. git pull läuft über einen eigenen SSH-Schlüssel, der nur dieses Repo
# lesen darf (GitHub → Settings → Deploy keys, ohne Schreibrecht). Bewusst nicht über den
# GitHub-Token des Workers: Der Agent soll die Regeln seiner eigenen Sandbox nicht lesen
# oder ändern können.
INFRA_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
INFRA_REPO=tripitest-art/agent-sandbox-infra
DEPLOY_KEY=/root/.ssh/agent_infra_deploy
install -d -m 700 /root/.ssh
[ -f "$DEPLOY_KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "ct112 agent-sandbox-infra (nur lesen)" -f "$DEPLOY_KEY"
# Host-Schlüssel von GitHub fest (api.github.com/meta, ssh_keys), kein Vertrauen beim ersten Kontakt
GH_HOSTKEY="github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
touch /root/.ssh/known_hosts; chmod 600 /root/.ssh/known_hosts
grep -qxF "$GH_HOSTKEY" /root/.ssh/known_hosts || echo "$GH_HOSTKEY" >> /root/.ssh/known_hosts
if ! grep -q '^Host github-infra$' /root/.ssh/config 2>/dev/null; then
  cat >> /root/.ssh/config <<CFG
Host github-infra
  HostName github.com
  User git
  IdentityFile $DEPLOY_KEY
  IdentitiesOnly yes
CFG
fi
chmod 600 /root/.ssh/config
# GitHub antwortet auch bei Erfolg mit Exit 1 (kein Shell-Zugang); entscheidend ist der Text.
# Erst in eine Variable: mit pipefail wäre „ssh … | grep“ sonst immer falsch.
auth=$(ssh -o BatchMode=yes -o ConnectTimeout=10 -T github-infra 2>&1 || true)
if grep -q "successfully authenticated" <<<"$auth"; then
  git -C "$INFRA_DIR" remote set-url origin "git@github-infra:$INFRA_REPO.git"
  echo "Deploy-Key: gültig, git pull läuft über SSH"
else
  echo "Deploy-Key: noch nicht bei GitHub eingetragen. In $INFRA_REPO → Settings → Deploy keys →"
  echo "Add deploy key, OHNE „Allow write access“, diesen öffentlichen Schlüssel eintragen:"
  cat "$DEPLOY_KEY.pub"
  echo "Danach dieses Skript erneut ausführen. Bis dahin bleibt git pull wie bisher (HTTPS)."
fi

echo "== Prüfung"
ok=$secrets_ok
gh --version | head -1
echo "Agent: $AGENT (Qwen Code $(qwen --version 2>/dev/null || echo fehlt)), Backend: $BACKEND"
if gh auth status >/dev/null 2>&1; then echo "GitHub: angemeldet"; else echo "GitHub: NICHT angemeldet (README, Schritt 3)"; ok=0; fi
if [ "$BACKEND" = litellm ]; then
  # Key nur an curl übergeben, nie ausgeben. 200 = Key gültig und Modell freigegeben.
  code=$(set -a; . /etc/qwen-worker.env 2>/dev/null; set +a
         curl -s -m 10 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${LITELLM_API_KEY:-}" \
           "$LITELLM/v1/models" || echo 000)
  if [ "$code" = 200 ]; then echo "LiteLLM: erreichbar, Key gültig ($LITELLM_MODEL)"
  else echo "LiteLLM: HTTP $code (Proxy aus oder Key falsch)"; ok=0; fi
elif curl -s -m 10 "$OLLAMA/api/tags" | grep -q "\"$MODEL"; then echo "Ollama: $MODEL vorhanden"
else echo "Ollama: $MODEL nicht erreichbar (PC aus, Bildmodus oder Modell fehlt)"; fi
# Nur zur Information: rote Tests der Projekte (oft fehlt in CT 112 nur ein Werkzeug) dürfen die
# Einrichtung nicht abbrechen, sonst bleiben die Timer unberührt (set -e, pipefail).
# SKIP_TESTS=1 überspringt den Lauf: Die Suite von sc-digger braucht auf CT 112 rund 5 Minuten (länger, wenn
# Worker gleichzeitig arbeiten), und die Skripte und Timer sind an dieser Stelle schon installiert.
if [ "${SKIP_TESTS:-0}" = 1 ]; then
  echo "(Testlauf von $REPO_DIR übersprungen: SKIP_TESTS=1)"
else
  (cd "$REPO_DIR" && /root/venv/bin/python -m pytest -q 2>&1 | tail -1) || echo "(Tests von $REPO_DIR nicht grün, siehe oben; Einrichtung läuft weiter)"
fi

# Welche Timer laufen, hat der Abschnitt Taktgeber oben entschieden (ein Timer oder Slots).
# Hier nur noch an/aus nach der Prüfung; nie zusätzlich qwen-worker.timer neben den Slots.
if [ "$MAX_WORKERS" -gt 1 ]; then worker_timers=$(seq -f "qwen-worker@%g.timer" 1 "$MAX_WORKERS"); else worker_timers=qwen-worker.timer; fi
if [ "$ok" = 1 ]; then
  for t in $worker_timers; do systemctl enable "$t" >/dev/null; systemctl restart "$t"; done   # neuer TAKT gilt sofort
  echo "Timer aktiv:"; systemctl list-timers 'qwen-worker*' qwen-review.timer --no-pager | sed '1d;/^$/,$d'
else
  for t in $worker_timers; do systemctl disable --now "$t" >/dev/null 2>&1 || true; done
  disable_slot_timers   # Reste aus einem früheren Lauf mit mehr Slots
  systemctl disable --now qwen-review.timer >/dev/null 2>&1 || true   # sonst postet und mergt der Review-Takt weiter
  echo "Timer bleibt aus, bis GitHub angemeldet und (bei litellm) der Key gültig ist. Danach dieses Skript erneut ausführen."
fi
