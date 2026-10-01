# 🏠 Home Assistant Integration & Dashboard

Dieses Modul bindet die Proxmox-Sandbox und den LLM-Host in Home Assistant ein:
1. **Hardware-Telemetrie:** Überwachung von GPU-Verbrauch (Watt), VRAM (GB), Temperaturen und Status des Gaming-PCs (Schläft, Fährt hoch, Ollama Online) über den WoL-Proxy.
2. **Live-Log-Streaming:** Echzeit-Stream der Gedankengänge des Coding-Agenten via MQTT.
3. **Fernsteuerung:** Manuelles Wecken des LLM-Hosts (Wake-on-LAN) und manuelles Anstoßen eines Worker-Taktes direkt aus der HA-App.

---

## 1. MQTT-Broker einrichten (Mosquitto Add-on)

1. Im Home Assistant unter **Einstellungen -> Add-ons** das offizielle **Mosquitto broker** Add-on installieren und starten.
2. Einen MQTT-Benutzer in der **Konfiguration der Mosquitto-App** anlegen (Abschnitt `logins`),
   nicht als HA-Benutzer – so öffnet das Passwort nur den Broker, nicht Home Assistant:
   * Benutzer: `LLM`
   * Ein eigenes, langes Passwort vergeben – **nie in dieses Repo schreiben** (es ist öffentlich).
   * Nach jeder Änderung an `logins` die Mosquitto-App **neu starten**, sonst gilt das alte Passwort weiter.
3. In der Sandbox `bash sandbox/install.sh` ausführen: Es installiert `mosquitto-clients` und
   `sandbox/systemd/qwen-mqtt.service` und fragt das Passwort verdeckt ab
   (gespeichert nur in `/etc/qwen-mqtt.env`, Rechte 600).

---

## 2. Sensoren konfigurieren

Kopiere den Inhalt aus [`configuration_sensors.yaml`](configuration_sensors.yaml) in deine `configuration.yaml` in Home Assistant und starte Home Assistant neu.

---

## 3. Dashboard-Karte anlegen

Erstelle eine neue Karte auf deinem Dashboard und füge den YAML-Code aus [`dashboard_card.yaml`](dashboard_card.yaml) ein.
