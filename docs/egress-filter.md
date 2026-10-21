# Egress-Filter der Sandbox (CT 112)

Diese Seite hält den **geplanten Ablauf und die belegten Entscheidungen** für
den Egress-Filter von `CT 112` fest. Sie ist **keine freigegebene Firewall-
Konfiguration und kein Runbook zum Anwenden von Regeln**.

## Status

Issue [#87](https://github.com/tripitest-art/agent-sandbox-infra/issues/87)
beschreibt den Filter als noch ausstehende Betriebsarbeit. Die dort geforderte
passive Mitschnitt-Phase, die endgültige Allowlist und die Aktivierung der
Firewall sind in diesem Repository nicht nachgewiesen. Insbesondere ist nicht
geprüft, ob die Container-Firewall aktiv ist oder ob `gh` und Qwen Code den
Forward-Proxy tatsächlich verwenden.

**Vor echten Messwerten und Prüfung durch Stephan keine Regeln aus dieser Seite
übernehmen.** Fehlende Angaben sind offene Arbeit, keine Erlaubnis.

## Zielbild

Nach Umsetzung soll `CT 112` nur die für den Betrieb notwendigen Ziele erreichen
können. Alles andere soll an der Proxmox-Firewall verworfen und protokolliert
werden. Issue #87 legt folgende Wege fest:

| Ziel | Zweck und geplanter Weg | Status / offene Prüfung |
| --- | --- | --- |
| LiteLLM (`CT 113`), Port 4000 | Modellaufrufe; direkte Firewall-Ausnahme | In #87 festgelegt; tatsächliche Adresse hier nicht dokumentiert |
| SearXNG (`CT 110`) | Dokumentationssuche; direkter Zugriff | In #87 festgelegt; benötigte Ports/Messwerte noch zu bestätigen |
| AdGuard (`CT 101`), Port 53 | Einziger DNS-Resolver | In #87 festgelegt; TCP/UDP-Regeln durch Stephan zu bestimmen |
| Home Assistant (HAOS-VM), Port 8123 | Direkter Webhook-Zugriff, ausdrücklich nicht über Caddy (`CT 103`) | Nur bis zum beschlossenen Rückbau; danach entfällt die Ausnahme |
| ntfy-Server | Dashboard-Push und Wake-Listener | Offen: `ntfy.sh` über Proxy oder eigener Server direkt |
| Neuer Forward-Proxy | GitHub, Paket-Registries und ggf. `ntfy.sh` | In #87 vorgesehen; Container, Adresse und Allowlist noch nicht dokumentiert |

Eingehend müssen bei aktivierter Container-Firewall zusätzlich SSH (Port 22)
von Stephans Rechner und das Dashboard (`DASHBOARD_PORT`, standardmäßig 8099)
aus dem LAN erreichbar bleiben. Die konkreten Quelladressen gehören nicht in
dieses öffentliche Repository.

`CT 103` (Caddy) ist kein erlaubter Weg zu den Diensten. Tailscale läuft laut
#87 nur in `CT 101` und ist keine Ausnahme für `CT 112`.

## Vorgehen laut Issue #87

1. **Passiv messen:** Verbindungen von `CT 112` an `veth112i0` mitschneiden und
   AdGuard-Abfragen für diesen Client protokollieren – mindestens einen
   normalen Arbeitstag einschließlich eines `install.sh`-Laufs. Die Ergebnisse
   bestimmen erst die tatsächlichen Ziele, Paket-Registries und Proxy-Namensliste.
2. **Forward-Proxy aufbauen:** Einen neuen kleinen Container (beispielsweise
   Squid) einrichten. Geplant sind ausschließlich HTTPS-`CONNECT`-Anfragen zu
   Namen aus der gemessenen Allowlist und Zugriff nur von `CT 112`. Danach
   `HTTPS_PROXY`/`NO_PROXY` für die Agentenprozesse konfigurieren und gesondert
   prüfen, ob Qwen Code und `gh` den Proxy verwenden. Diese Kompatibilität ist
   laut #87 bislang nicht geprüft.
3. **Firewall zuletzt aktivieren:** Datacenter- und Container-Firewall mit
   ausgehendem Standard `DROP`, protokollierten Ausnahmen und den nötigen
   eingehenden SSH-/Dashboard-Regeln einrichten. Vor jeder Änderung muss ein
   automatischer Rückbau nach zehn Minuten vorbereitet sein; Stephan hebt ihn
   erst nach erfolgreicher Prüfung auf. Proxmox-Konsole bereithalten.
4. **Proxy und Firewall nicht am selben Tag aufbauen.** So bleibt die Ursache
   bei Problemen eingrenzbar.

Die konkrete Proxmox-Syntax, Interfaces, Quell-/Zieladressen und Proxy-Konfiguration
werden hier absichtlich nicht angegeben: Sie müssen zum gemessenen und geprüften
Aufbau passen.

## Rückbau und Fehlersuche

Der in #87 vorgesehene Sicherheitsmechanismus ist ein **vorab eingerichteter
automatischer Rückbau nach zehn Minuten** bei Firewall-Änderungen. Stephan prüft
die Änderung und hebt den Rückbau erst dann auf. Die Proxmox-Konsole muss
erreichbar sein.

Bei einem blockierten Lauf zuerst feststellen, ob die Ursache DNS, Proxy,
Allowlist oder Firewall ist. Fehlende Ziele erst nach Prüfung der passiven
Messwerte und der tatsächlichen Anwendung ergänzen. Diese Seite erteilt keine
Freigabe, die Firewall pauschal auf `ACCEPT` zu setzen oder den Container ohne
Filter ans LAN zu hängen; konkrete Rückbau-Befehle müssen für den realen
Proxmox-Aufbau von Stephan dokumentiert und geprüft werden.

## Grenzen

- Der Filter verhindert keinen Abfluss über erlaubte Ziele. #87 nennt
  insbesondere GitHub (z. B. Kommentare oder Gists mit dem Worker-Token) und
  Inhalte von SearXNG-Anfragen. Ein eng begrenzter Token ist eine separate
  Gegenmaßnahme.
- Die vollständige Namensliste für GitHub, Paketquellen und ggf. ntfy ist offen
  und muss aus der Mitschnitt-Phase hervorgehen. Namen wie `api.github.com`,
  PyPI-Hosts oder `ntfy.sh` sind nicht allein durch diese Doku als freigegebene
  Ziele bestätigt.
- Ob `firewall=1` an der Netzwerkkarte genügt bzw. die Container-Firewall
  (`/etc/pve/firewall/112.fw`) aktiv ist, war laut #87 nicht geprüft.
- Die HAOS-Ausnahme gilt nur bis zum beschlossenen Home-Assistant-Rückbau; der
  tatsächliche Abschluss ist hier nicht belegt.
- Keine IP-Adressen, Zugangsdaten, Schlüssel oder Webhook-IDs eintragen.
- Host-Konfiguration und Installation bleiben Stephans Aufgabe. Diese Seite
  ändert weder Proxmox noch `install.sh`, `worker_tick.py` oder `review_tick.py`.
