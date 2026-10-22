#!/usr/bin/env bash
# Hilfsfunktionen für install.sh (hier getrennt, damit tests/test_install_lib.py sie ohne Server prüft).
# Wird mit `source` geladen; setzt kein `set -e` selbst und braucht SCRIPT_DIR und INFRA_DIR.

# --- Gemerkte Einstellungen ---------------------------------------------------------------------
# Ein erneuter Lauf von install.sh (z. B. nach git pull) soll Einstellungen wie MAX_WORKERS oder
# REVIEW_POST nicht still zurücksetzen. Sie stehen in /etc/agent-sandbox.conf (KEY=Wert, root).
# Vorrang: ausdrücklich gesetzte Umgebung > gemerkter Wert > Vorgabe.
CONF=${AGENT_SANDBOX_CONF:-/etc/agent-sandbox.conf}

conf_get() {   # conf_get KEY → gemerkter Wert oder nichts; nie ein Fehlerstatus (set -e)
  local line=""
  if [ -f "$CONF" ]; then
    line=$(grep -E "^$1=[A-Za-z0-9._,@-]*$" "$CONF" | tail -1 || true)
  fi
  printf '%s' "${line#*=}"
  return 0
}

# Schreibt nur die genannten Schlüssel neu und behält alle übrigen Zeilen: Ein von Hand ergänzter
# Schlüssel (den install.sh nicht kennt) überlebt so den nächsten Lauf. Modus 600 wie bei den
# Geheimnisdateien daneben (ask_secret) – die Datei könnte einmal einen Wert mit Geheimnis enthalten.
conf_save() {   # conf_save KEY… → schreibt den aktuellen Wert der genannten Variablen
  local tmp="$CONF.tmp" k v pattern=""
  for k in "$@"; do
    v=${!k-}
    [[ $v =~ ^[A-Za-z0-9._,@-]*$ ]] || { echo "Einstellung $k hat unzulässige Zeichen, nicht gemerkt" >&2; return 1; }
    pattern+="${pattern:+|}^$k="
  done
  (
    umask 077
    # Vorhandene Zeilen lesen und nur die übergebenen Schlüssel herausfiltern; dann die neuen
    # Werte anhängen. `|| true`: Eine Datei, in der keine alte Zeile passt, ist kein Fehler.
    if [ -f "$CONF" ]; then
      grep -Ev "$pattern" "$CONF" || true
    fi
    for k in "$@"; do printf '%s=%s\n' "$k" "${!k-}"; done
  ) > "$tmp"
  mv "$tmp" "$CONF"
  chmod 600 "$CONF"
}

# --- Agent und Context-Mode ----------------------------------------------------------------------
# AGENT und CONTEXT_MODE gehören seit #270 in die Einstellungsdatei: Ohne den Merker fiele ein
# späterer Lauf (auch der Auto-Deploy) still auf qwen-code ohne context-mode zurück.
agent_check() {   # prüft AGENT (qwen-code|opencode) und CONTEXT_MODE (leer|0|1); 1 bei Unsinn
  local ok=0
  case ${AGENT:-qwen-code} in
    qwen-code|opencode) ;;
    *) echo "AGENT=${AGENT-} ist ungültig (erlaubt: qwen-code oder opencode)." >&2; ok=1 ;;
  esac
  [[ ${CONTEXT_MODE-} =~ ^(0|1)?$ ]] \
    || { echo "CONTEXT_MODE=${CONTEXT_MODE-} ist ungültig (erlaubt: 0, 1 oder leer)." >&2; ok=1; }
  return $ok
}

# Gibt /root/.config/opencode/opencode.json auf stdout aus. Rückfrage-Werkzeuge sind gesperrt,
# weil der Takt ohne Terminal läuft; external_directory hält den Agenten im Worktree. Die
# Sampling-Werte folgen der Model-Card von Qwen3-Coder (temperature 0.7, top_p 0.8).
opencode_config_json() {
  local model plugin_line=""
  if [ "$BACKEND" = litellm ]; then
    model="litellm/$LITELLM_MODEL"
  else
    model="ollama/$MODEL"
  fi
  if [ "${CONTEXT_MODE:-0}" = 1 ]; then
    plugin_line='
  "plugin": ["context-mode"],'
  fi
  cat <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (Gaming-PC)",
      "options": { "baseURL": "$OLLAMA/v1" },
      "models": { "$MODEL": { "name": "$MODEL", "options": { "temperature": 0.7, "topP": 0.8 } } }
    },
    "litellm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "LiteLLM (LXC 113)",
      "options": { "baseURL": "$LITELLM/v1", "apiKey": "{env:LITELLM_API_KEY}" },
      "models": { $(for m in $LITELLM_ALL; do printf '"%s": { "name": "%s" }, ' "$m" "$m"; done | sed 's/, $//') }
    }
  },
  "model": "$model",$plugin_line
  "permission": { "edit": "allow", "bash": "allow", "webfetch": "deny", "question": "deny", "plan_enter": "deny", "plan_exit": "deny", "external_directory": "deny" }
}
JSON
}

# --- Projekte -------------------------------------------------------------------------------------
project_lines() {   # Name, Repo, Klon je Projekt (Tab); bricht bei kaputter Projektliste ab
  python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
import projects
try:
    for p in projects.load_projects():
        print(p.name, p.repo, p.workdir, sep="\t")
except projects.ProjectConfigError as e:
    sys.exit(f"Projektliste unbrauchbar: {e}")
' "$SCRIPT_DIR"
}

# Hält den Basis-Klon auf dem Stand von origin/main. Ein Klon, der auf einem alten Feature-Branch
# hängt, ließ schon Tests rot werden (#202-Branch auf CT 112). Nichts geht verloren: der alte Branch
# bleibt bestehen, nur HEAD wechselt. Mit lokalen Änderungen wird nichts angefasst, nur gewarnt.
# HEAD wird abgelöst (--detach), den Branch `main` belegt der Klon NICHT: Die Worker-Worktrees
# (<workdir>-worktrees/w<N>) checken `main` aus, und Git erlaubt einen Branch nur an einer Stelle.
# Mit `checkout -B main` hielt der Basis-Klon ihn fest, und beide Slots scheiterten mit
# „'main' is already checked out“ (14.10.).
sync_clone_to_main() {   # $1 = Name, $2 = Klon-Ordner
  local name=$1 dir=$2
  if ! git -C "$dir" fetch -q origin main; then
    echo "Projekt $name: fetch fehlgeschlagen, Klon bleibt wie er ist" >&2
    return 0
  fi
  if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
    echo "Projekt $name: lokale Änderungen in $dir, Klon wird nicht auf main gesetzt" >&2
    return 0
  fi
  git -C "$dir" checkout -q --detach origin/main || { echo "Projekt $name: Wechsel auf origin/main fehlgeschlagen" >&2; return 0; }
  echo "Projekt $name: Klon steht auf origin/main"
}

ensure_projects() {   # klont fehlende Projekte, hält sie auf main und legt ihre Worktree-Ordner an
  local lines name repo workdir
  lines=$(project_lines) || return 1
  while IFS=$'\t' read -r name repo workdir; do
    [ -n "$name" ] || continue
    # Der Worker setzt seinen Klon mit reset --hard und clean zurück; der Deploy-Klon dieser
    # Infrastruktur (hier läuft git pull) darf das nie sein.
    if [ "$(realpath -m "$workdir")" = "$(realpath -m "$INFRA_DIR")" ]; then
      echo "Projekt $name: workdir $workdir ist der Deploy-Klon dieser Infrastruktur. Bitte einen eigenen Ordner" >&2
      echo "nehmen (z. B. /root/work/$name), sonst setzt der Worker ihn zurück." >&2
      return 1
    fi
    if [ -d "$workdir/.git" ]; then
      echo "Projekt $name: Klon vorhanden ($workdir)"
      sync_clone_to_main "$name" "$workdir"
    else
      git clone -q "https://github.com/$repo.git" "$workdir" || { echo "Projekt $name: Klonen von $repo fehlgeschlagen" >&2; return 1; }
      echo "Projekt $name: $repo nach $workdir geklont"
    fi
    if [ "${MAX_WORKERS:-1}" -gt 1 ]; then mkdir -p "${workdir%/}-worktrees"; fi
  done <<<"$lines"
}

project_workdirs() {   # nur die Klon-Ordner, je Zeile einer
  project_lines | cut -f3
}

# --- Ressourcenlimits je Worker-Slot --------------------------------------------------------------
# Bis zu fünf Slots teilen sich einen Container; ohne Grenze zwischen ihnen reißt ein außer Kontrolle
# geratener Lauf den RAM aller anderen mit (und Review und Webhook gleich mit). Die Grenzen landen
# als MemoryMax/TasksMax/CPUQuota in qwen-worker@.service. Leer oder „none“ heißt kein Limit, damit
# ein bestehender Betrieb sich nicht ändert. Die CPU-Grenze steht als reine Zahl (Prozent) in der
# Einstellungsdatei, weil conf_save kein „%“ zulässt; das Prozentzeichen setzt slot_limit_lines.
slot_limits_check() {   # prüft SLOT_MEMORY_MAX, SLOT_TASKS_MAX, SLOT_CPU_PERCENT; 1 bei Unsinn
  local ok=0
  [[ ${SLOT_MEMORY_MAX-} =~ ^([1-9][0-9]*[KMGT]?|none)?$ ]] \
    || { echo "SLOT_MEMORY_MAX=${SLOT_MEMORY_MAX-} ist ungültig (z. B. 1200M, 2G oder none)." >&2; ok=1; }
  [[ ${SLOT_TASKS_MAX-} =~ ^([1-9][0-9]*|none)?$ ]] \
    || { echo "SLOT_TASKS_MAX=${SLOT_TASKS_MAX-} ist ungültig (Zahl >= 1 oder none)." >&2; ok=1; }
  [[ ${SLOT_CPU_PERCENT-} =~ ^([1-9][0-9]*|none)?$ ]] \
    || { echo "SLOT_CPU_PERCENT=${SLOT_CPU_PERCENT-} ist ungültig (Prozent als Zahl >= 1, z. B. 150, oder none)." >&2; ok=1; }
  return $ok
}

slot_limit_lines() {   # Zeilen für den [Service]-Block; nichts für leere Einstellungen und „none“
  [ -z "${SLOT_MEMORY_MAX-}" ] || [ "$SLOT_MEMORY_MAX" = none ] || echo "MemoryMax=$SLOT_MEMORY_MAX"
  [ -z "${SLOT_TASKS_MAX-}" ] || [ "$SLOT_TASKS_MAX" = none ] || echo "TasksMax=$SLOT_TASKS_MAX"
  [ -z "${SLOT_CPU_PERCENT-}" ] || [ "$SLOT_CPU_PERCENT" = none ] || echo "CPUQuota=$SLOT_CPU_PERCENT%"
  return 0
}

# --- Logrotation ---------------------------------------------------------------------------------
# worker.log (alle Takte) und audit.jsonl wachsen ohne Grenze; das Dashboard liest beim Start nur das
# Ende der Datei, die Platte des Containers ist klein. copytruncate ist nötig, weil systemd
# worker.log über StandardOutput=append: offen hält und ein Umbenennen nicht mitbekäme.
logrotate_conf() {   # gibt die logrotate-Einstellung auf stdout aus; $LOG_ROTATE_SIZE (Vorgabe 50M)
  local size=${LOG_ROTATE_SIZE:-50M}
  [[ $size =~ ^[1-9][0-9]*[kMG]$ ]] \
    || { echo "LOG_ROTATE_SIZE=$size ist ungültig (Zahl >= 1 mit k, M oder G, z. B. 50M)." >&2; return 1; }
  cat <<CONF
/root/worker.log /root/audit.jsonl {
    size $size
    rotate 4
    compress
    delaycompress
    copytruncate
    missingok
    notifempty
}
CONF
}
