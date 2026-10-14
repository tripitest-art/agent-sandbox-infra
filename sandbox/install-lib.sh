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

conf_save() {   # conf_save KEY… → schreibt den aktuellen Wert der genannten Variablen
  local tmp="$CONF.tmp" k v
  for k in "$@"; do
    v=${!k-}
    [[ $v =~ ^[A-Za-z0-9._,@-]*$ ]] || { echo "Einstellung $k hat unzulässige Zeichen, nicht gemerkt" >&2; return 1; }
  done
  ( umask 022; for k in "$@"; do printf '%s=%s\n' "$k" "${!k-}"; done > "$tmp" )
  mv "$tmp" "$CONF"
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

ensure_projects() {   # klont fehlende Projekte und legt ihre Worktree-Ordner an
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
