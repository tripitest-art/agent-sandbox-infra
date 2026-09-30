# 📦 Proxmox LXC Container Setup für den Coding-Agenten

Der Coding-Agent läuft isoliert in einem unprivilegierten Debian 12 LXC-Container auf Proxmox VE. 
Dadurch hat er keinen Zugriff auf das Host-System, das Heimnetz oder vertrauliche Datenspeicher.

---

## 1. Container anlegen (auf dem Proxmox Host)

Auf dem Proxmox VE Host (z. B. via Web-Shell oder SSH als root):

```bash
CTID=$(pvesh get /cluster/nextid) bash host-create.sh
```

Das Skript:
* Lädt das neueste `debian-12-standard` Template herunter (falls nicht vorhanden).
* Erstellt einen unprivilegierten Container mit 4 Cores, 4 GB RAM, 1 GB Swap, 20 GB Disk.
* Aktiviert `nesting=1`.
* Startet den Container und initialisiert das Setup.

---

## 2. SSH-Zugang für Steuerungs-Instanzen einrichten

Auf dem lokalen Entwicklungsrechner (oder Steuer-Agenten) einen SSH-Key generieren:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/agent-sandbox -N "" -C "agent-sandbox"
cat ~/.ssh/agent-sandbox.pub
```

Den öffentlichen Schlüssel auf dem Proxmox-Host in den Container einfügen (`<CTID>` anpassen):

```bash
read -rp "Öffentlicher Schlüssel: " K
pct exec <CTID> -- bash -c "mkdir -p -m 700 /root/.ssh && echo '$K' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && systemctl enable --now ssh"
```

In `~/.ssh/config` auf dem lokalen Rechner:

```sshconfig
Host agent-sandbox
    HostName <CONTAINER_IP>
    User root
    IdentityFile ~/.ssh/agent-sandbox
    IdentitiesOnly yes
```
