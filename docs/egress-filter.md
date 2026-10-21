# Egress-Filter der Sandbox (CT 112)

Zielbild, Regeln, Rückbau und Grenzen des ausgehenden Netzverkehrs für den
Sandbox-Container `CT 112` (`agent-sandbox`). Diese Anleitung macht den Filter
nach einem Neuaufbau des Proxmox-Hosts wiederholbar; echte Werte (IPs,
Namenslisten, Registries) stehen dort, wo Stephan sie in der Mitschnitt-Phase
notiert hat, sonst als Platzhalter `<von Stephan zu ergänzen>`.

## Zielbild

`CT 112` ist unprivilegiert und darf nur zu den für den Betrieb nötigen Zielen
kommunizieren. Alles andere ist gesperrt. Erlaubte Ziele:

- **CT 113** (LiteLLM-Proxy, `:4000`) – Modellaufrufe der Worker und des Review-Takts.
- **CT 110** (SearXNG) – Websuche, falls aktiviert.
- **CT 101** (AdGuard, DNS) – Namensauflösung für alle Container.
- **CT 103** (Caddy) – ausdrücklich **gesperrt**; kein Zugriff von `CT 112`.
- **ntfy** – Push-Meldungen und Wake-Stream (`WAKE_URL`, Dashboard-Thema).
- **Dashboard-Port 8099** – nur LAN, kein Egress ins Internet.
- **SSH (Port 22)** – nur eingehend als Ausnahme (Verwaltung).
- **GitHub** – `api.github.com` für Issues, PRs, Workflows, Releases.
- **Python-Pakete** – `files.pythonhosted.org` (und die PyPI-Index-Seite).
- **Home Assistant (Port 8123)** – **nicht** Teil des Zielbilds; zurückgebaut.

Zugriff auf `CT 113`, `CT 110`, `CT 101` erfolgt über die Proxmox-Bridge im
LAN; Internet-Ziele nur über die unten genannten Namen.

## Regeln

Die Regeln werden auf dem **Proxmox-Host** (nicht im Container) gesetzt, z. B.
per `iptables`/`nftables` oder Proxmox-Firewall. Adressen stehen als
Platzhalter (`<LITELLM-IP>`, `<SEARXNG-IP>`, `<ADGUARD-IP>`,
`<CONTAINER-IP>`); die Namensliste für Internet-Ziele wird über den Forward-Proxy
aufgelöst.

1. Default-Policy für `CT 112`: `DROP` für ausgehend (`OUTPUT`/`FORWARD`).
2. Erlaube DNS zu `CT 101` (AdGuard, Port 53, TCP/UDP).
3. Erlaube HTTPS zu `CT 113` (LiteLLM, `<LITELLM-IP>:4000`).
4. Erlaube HTTP/HTTPS zu `CT 110` (SearXNG, `<SEARXNG-IP>`).
5. Erlaube HTTPS zu den Internet-Namen `api.github.com` und
   `files.pythonhosted.org` (über Forward-Proxy; Namensliste siehe unten).
6. Erlaube ntfy (HTTPS, `ntfy.sh` oder selbst gehostete Instanz) für Push und
   Wake-Stream.
7. Erlaube eingehend SSH (Port 22) als Ausnahme für Verwaltung.
8. Verbiete explizit `CT 103` (Caddy, Port 80/443) – keine Ausnahme.
9. Verbiete Home Assistant (Port 8123) – zurückgebaut, kein Zielbild.

### Namensliste des Forward-Proxys

Der Forward-Proxy (auf dem Host oder in `CT 103`-Nachbarschaft, aber nicht
erreichbar von `CT 112`) löst folgende Namen auf und lässt nur diese passieren:

- `api.github.com`
- `files.pythonhosted.org`
- `pypi.org` (Index, `<von Stephan zu ergänzen>` falls genutzt)
- ntfy-Host (`ntfy.sh` oder selbst gehostet, `<von Stephan zu ergänzen>`)
- `<von Stephan zu ergänzen>` – weitere Registries/CDN-Endpunkte aus der
  Mitschnitt-Phase.

Regel: Nur HTTPS (Port 443) zu diesen Namen; HTTP (80) wird nicht weitergeleitet.

## Rückbau

Wenn ein Lauf blockiert wird (Worker/Review bricht mit Netzwerkfehler ab),
kann der Filter schrittweise zurückgebaut werden, ohne den Container neu
aufzusetzen:

1. Log prüfen: `journalctl -u qwen-worker@1` / `qwen-review` auf
   `Connection refused` / `timeout` / `Name or service not known`.
2. Betroffenes Ziel identifizieren (Host, Port, Name).
3. Regel ergänzen oder temporär lockern:
   - Entweder den Namen in die Forward-Proxy-Liste aufnehmen.
   - Oder die IP/Port-Kombination in der Host-Firewall freigeben.
4. Testlauf: `systemctl start qwen-worker@1` und beobachten.
5. Dauerhafte Aufnahme erst nach erfolgreichem Lauf; temporäre Lockerung
   wieder entfernen, sobald der Grund geklärt ist.

Filter komplett abschalten (Notfall, nur kurzzeitig):

- Host-Firewall-Regel für `CT 112` auf `ACCEPT` setzen.
- Alternativ Container-Netzwerk auf `vmbr0` ohne Filter hängen.
- Nach Ursachenklärung Filter wieder aktivieren und Regel ergänzen.

Home Assistant (Port 8123) bleibt ausgeschlossen, auch im Rückbau.

## Grenzen

- Keine Skripte, Timer oder Änderungen an `install.sh`, `worker_tick.py`,
  `review_tick.py` oder `proxmox/host-create.sh` in dieser Doku.
- Keine IP-Adressen, Schlüssel oder Webhook-IDs; Adressen als Platzhalter
  (`<LITELLM-IP>` usw.).
- Die tatsächlichen Werte aus der Mitschnitt-Phase (Namensliste, Registries)
  liefert Stephan im Issue-Kommentar; fehlende Werte stehen als
  `<von Stephan zu ergänzen>`.
- Die Doku beschreibt das Zielbild aus Issue #87; sie ersetzt keine
  Host-Konfiguration und enthält keine ausführbaren Befehle für den Container.
- Gültig für `CT 112` (`agent-sandbox`); andere Container (CT 106, CT 113)
  haben eigene Regeln.
path: C:\Users\user\Documents\agent-sandbox-infra-work\docs\egress-filter.md