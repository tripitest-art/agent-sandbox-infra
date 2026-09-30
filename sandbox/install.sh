#!/usr/bin/env bash
# Richtet die Worker-Sandbox ein oder bringt sie auf den Stand von main. Läuft IM Container
# (CT 112) als root und darf beliebig oft laufen. Anleitung: sandbox/README.md.
#
#   bash /root/sc-digger/sandbox/install.sh            # einrichten / aktualisieren
#   MODEL=qwen3-coder-64k OLLAMA=http://… bash …       # andere Werte als die Vorgaben
#   AGENT=opencode bash …                              # OpenCode statt Qwen Code
#   WAKE_PC=1 TAKT=30min bash …                        # PC bei Arbeit wecken, seltener schauen
#
# Der GitHub-Zugang wird hier bewusst NICHT eingerichtet: den Token gibt Stephan selbst
# verdeckt ein (README, Schritt 3). Das Skript prüft nur, ob er da ist.
set -euo pipefail

REPO_DIR=/root/sc-digger
OLLAMA=${OLLAMA:-http://192.168.0.210:11434}
MODEL=${MODEL:-qwen3-coder-64k}
AGENT=${AGENT:-qwen-code}                       # oder opencode
TAKT=${TAKT:-5min}                              # so oft sucht der Worker Arbeit
WAKE_PC=${WAKE_PC:-0}                           # 1: Gaming-PC bei Arbeit per WoL wecken
QWEN_CODE_VERSION=${QWEN_CODE_VERSION:-0.24.7}  # fest: ein Update soll bewusst passieren
export DEBIAN_FRONTEND=noninteractive

echo "== Pakete"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip ffmpeg \
  libsndfile1 libchromaprint-tools >/dev/null
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
    }
  },
  "model": "ollama/$MODEL",
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
      }
    ]
  },
  "security": { "auth": { "selectedType": "openai" } },
  "model": { "name": "$MODEL" },
  "privacy": { "usageStatisticsEnabled": false },
  "telemetry": { "enabled": false }
}
JSON

echo "== Taktgeber"
install -m 755 "$REPO_DIR/sandbox/worker_tick.py" /root/worker_tick.py
cat > /etc/systemd/system/qwen-worker.service <<UNIT
[Unit]
Description=Qwen-Worker: ein Auftrag pro Takt (sandbox/worker_tick.py)
[Service]
Type=oneshot
# Ohne HOME finden gh, git und OpenCode ihre Konfiguration nicht (gh endet mit Exit 4).
Environment=HOME=/root
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
systemctl daemon-reload

echo "== Prüfung"
ok=1
gh --version | head -1
echo "Agent: $AGENT (Qwen Code $(qwen --version 2>/dev/null || echo fehlt))"
if gh auth status >/dev/null 2>&1; then echo "GitHub: angemeldet"; else echo "GitHub: NICHT angemeldet (README, Schritt 3)"; ok=0; fi
if curl -s -m 10 "$OLLAMA/api/tags" | grep -q "\"$MODEL"; then echo "Ollama: $MODEL vorhanden"
else echo "Ollama: $MODEL nicht erreichbar (PC aus, Bildmodus oder Modell fehlt)"; fi
(cd "$REPO_DIR" && /root/venv/bin/python -m pytest -q 2>&1 | tail -1)

if [ "$ok" = 1 ]; then
  systemctl enable qwen-worker.timer >/dev/null
  systemctl restart qwen-worker.timer   # neuer TAKT gilt sofort
  echo "Timer aktiv:"; systemctl list-timers qwen-worker.timer --no-pager | sed -n 2p
else
  systemctl disable --now qwen-worker.timer >/dev/null 2>&1 || true
  echo "Timer bleibt aus, bis GitHub angemeldet ist. Danach dieses Skript erneut ausführen."
fi
