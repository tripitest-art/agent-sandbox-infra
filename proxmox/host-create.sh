#!/usr/bin/env bash
# Auf dem Proxmox-HOST ausführen (nur Stephan): legt den Sandbox-Container an und startet
# darin sandbox/install.sh. Anleitung: sandbox/README.md, Schritt 1.
#
#   CTID=$(pvesh get /cluster/nextid) bash host-create.sh
set -euo pipefail

CTID=${CTID:?CTID setzen, z. B. CTID=$(pvesh get /cluster/nextid)}
STORAGE=${STORAGE:-local-lvm}
BRIDGE=${BRIDGE:-vmbr0}

pct status "$CTID" >/dev/null 2>&1 && { echo "CT $CTID existiert schon."; exit 1; }

pveam update >/dev/null
TPL=$(pveam available --section system | awk '/debian-12-standard/{print $2}' | sort -V | tail -1)
[ -n "$TPL" ] || { echo "Kein Debian-12-Template gefunden"; exit 1; }
pveam list local | grep -q "$TPL" || pveam download local "$TPL"

# Unprivilegiert, keine Mounts: kein Zugriff auf Sammlung, Inbox oder andere Container.
pct create "$CTID" "local:vztmpl/$TPL" --hostname agent-sandbox \
  --cores 4 --memory 4096 --swap 1024 --rootfs "$STORAGE:20" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" --unprivileged 1 --features nesting=1 \
  --onboot 1 --start 1
sleep 8

pct exec "$CTID" -- bash -c 'apt-get update -qq && apt-get install -y -qq git ca-certificates >/dev/null \
  && git clone -q https://github.com/tripitest-art/sc-digger.git /root/sc-digger'
pct exec "$CTID" -- bash /root/sc-digger/sandbox/install.sh
echo "IP des Containers: $(pct exec "$CTID" -- hostname -I)"
echo "Weiter mit README Schritt 2 (SSH-Schlüssel) und 3 (GitHub-Token)."
