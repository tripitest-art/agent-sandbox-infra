#!/usr/bin/env bash
# Richtet die Worker-Sandbox ein oder bringt sie auf den Stand von main. Läuft IM Container
# (CT 112) als root und darf beliebig oft laufen. Anleitung: README.md.
#
#   bash /root/agent-sandbox-infra/sandbox/install.sh  # einrichten / aktualisieren
#   BACKEND=ollama bash …                              # Qwen3-Coder auf dem Gaming-PC statt LiteLLM
#   MODEL=qwen3-coder-64k OLLAMA=http://… bash …       # andere Werte als die Vorgaben
#   AGENT=opencode bash …                              # OpenCode statt Qwen Code
#   WAKE_PC=1 TAKT=30min bash …                        # PC bei Arbeit wecken, seltener schauen
#
# Der GitHub-Zugang wird hier bewusst NICHT eingerichtet: den Token gibt Stephan selbst
# verdeckt ein (README, Schritt 3). Das Skript prüft nur, ob er da ist.
# Geheimnisse stehen nie im Repo (es ist öffentlich): Der LiteLLM-Key kommt nach
# /etc/qwen-worker.env, die Webhook-URL für das Live-Log nach /etc/qwen-webhook.env (beide 600).
# Fehlen sie, fragt das Skript verdeckt danach (nur im Terminal).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)   # sandbox/ dieses Repos
REPO_DIR=/root/sc-digger
BACKEND=${BACKEND:-litellm}                     # litellm (DeepSeek über LXC 113) oder ollama
LITELLM=${LITELLM:-http://192.168.0.211:4000}
LITELLM_MODEL=${LITELLM_MODEL:-coder}           # Alias im LiteLLM-Proxy
# Ausweichmodelle, wenn LITELLM_MODEL die Probe vor dem Lauf nicht besteht (worker_tick.py)
# coder-or = dasselbe DeepSeek über OpenRouter (weicht dort auf andere Anbieter aus), dann Codestral
LITELLM_FALLBACKS=${LITELLM_FALLBACKS:-coder-or,coder-free}
OLLAMA=${OLLAMA:-http://192.168.0.210:11434}
MODEL=${MODEL:-qwen3-coder-64k}
AGENT=${AGENT:-qwen-code}                       # oder opencode
TAKT=${TAKT:-5min}                              # so oft sucht der Worker Arbeit
WAKE_PC=${WAKE_PC:-0}                           # 1: Gaming-PC bei Arbeit per WoL wecken
QWEN_CODE_VERSION=${QWEN_CODE_VERSION:-0.24.7}  # fest: ein Update soll bewusst passieren
export DEBIAN_FRONTEND=noninteractive
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
[ -d "$REPO_DIR/.git" ] || git clone -q https://github.com/tripitest-art/sc-digger.git "$REPO_DIR"
[ -x /root/venv/bin/python ] || python3 -m venv /root/venv
/root/venv/bin/pip install -q -r "$REPO_DIR/requirements.txt" pytest
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
install -m 755 "$SCRIPT_DIR/worker_tick.py" /root/worker_tick.py
cat > /etc/systemd/system/qwen-worker.service <<UNIT
[Unit]
Description=Qwen-Worker: ein Auftrag pro Takt (sandbox/worker_tick.py)
[Service]
Type=oneshot
# Ohne HOME finden gh, git und OpenCode ihre Konfiguration nicht (gh endet mit Exit 4).
Environment=HOME=/root
# LITELLM_API_KEY (BACKEND=litellm); fehlt die Datei, startet der Takt trotzdem (ollama).
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
ExecStart=/usr/bin/flock -n /run/qwen-worker.lock /usr/bin/python3 /root/worker_tick.py
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

echo "== Log-Stream nach Home Assistant (Webhook)"
# Früher MQTT: Der Login konnte am Broker alles lesen/schreiben (Zendure, Frigate, Zigbee);
# die ACL der Mosquitto-App greift nicht. Alten Dienst und sein Passwort entfernen.
if [ -e /etc/systemd/system/qwen-mqtt.service ] || [ -e /etc/qwen-mqtt.env ]; then
  systemctl disable --now qwen-mqtt.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/qwen-mqtt.service
  [ -e /etc/qwen-mqtt.env ] && shred -u /etc/qwen-mqtt.env
  echo "qwen-mqtt: entfernt (inkl. /etc/qwen-mqtt.env)"
fi
install -m 755 "$SCRIPT_DIR/qwen_webhook.py" /root/qwen_webhook.py
install -m 644 "$SCRIPT_DIR/systemd/qwen-webhook.service" /etc/systemd/system/qwen-webhook.service
systemctl daemon-reload
if [ -f /etc/qwen-webhook.env ] && grep -q '^HA_WEBHOOK_URL=.' /etc/qwen-webhook.env; then
  systemctl enable qwen-webhook.service >/dev/null; systemctl restart qwen-webhook.service
  echo "qwen-webhook: $(systemctl is-active qwen-webhook.service)"
else
  systemctl disable --now qwen-webhook.service >/dev/null 2>&1 || true
  echo "qwen-webhook: aus (/etc/qwen-webhook.env fehlt)"
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
(cd "$REPO_DIR" && /root/venv/bin/python -m pytest -q 2>&1 | tail -1)

if [ "$ok" = 1 ]; then
  systemctl enable qwen-worker.timer >/dev/null
  systemctl restart qwen-worker.timer   # neuer TAKT gilt sofort
  echo "Timer aktiv:"; systemctl list-timers qwen-worker.timer --no-pager | sed -n 2p
else
  systemctl disable --now qwen-worker.timer >/dev/null 2>&1 || true
  echo "Timer bleibt aus, bis GitHub angemeldet und (bei litellm) der Key gültig ist. Danach dieses Skript erneut ausführen."
fi
