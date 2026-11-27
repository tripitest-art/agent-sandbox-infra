#!/usr/bin/env bash
# Installiert die pre-receive-Sperre in jedes Git-Repo unter <Verzeichnis>
# (Vorgabe /config/projects). Beliebig oft lauffähig; ein fremder pre-receive-Hook bleibt
# stehen und wird nur gemeldet. Aufruf auf CT 106: dev darf die Repos selbst beschreiben.
set -euo pipefail
ZIEL=${1:-${PROJECTS_DIR:-/config/projects}}
QUELLE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pre-receive-sperre
gefunden=0
for repo in "$ZIEL"/*/; do
  [ -d "${repo}.git" ] || continue
  gefunden=$((gefunden + 1))
  hook="${repo}.git/hooks/pre-receive"
  install -d "$(dirname "$hook")"
  if cmp -s "$QUELLE" "$hook"; then
    # Ohne X-Bit ignoriert Git den Hook: gleicher Inhalt heißt dann nicht "aktiv".
    if [ -x "$hook" ]; then
      echo "Sperre aktiv: ${repo%/}"
    else
      install -m 755 "$QUELLE" "$hook"
      echo "Sperre repariert: ${repo%/}"
    fi
  elif [ -e "$hook" ]; then
    echo "FREMDER HOOK: $hook weicht ab, nicht überschrieben" >&2
  else
    install -m 755 "$QUELLE" "$hook"
    echo "Sperre installiert: ${repo%/}"
  fi
done
[ "$gefunden" -gt 0 ] || echo "Keine Git-Repos unter $ZIEL gefunden."
