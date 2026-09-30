# 🏠 Home Assistant Integration & Dashboard

Dieses Modul bindet die Proxmox-Sandbox und den LLM-Host in Home Assistant ein:
1. **Hardware-Telemetrie:** Überwachung von GPU-Verbrauch (Watt), VRAM (GB), Temperaturen und Status des Gaming-PCs (Schläft, Fährt hoch, Ollama Online) über den WoL-Proxy.
2. **Live-Log-Streaming:** Echzeit-Stream der Gedankengänge des Coding-Agenten via MQTT.
3. **Fernsteuerung:** Manuelles Wecken des LLM-Hosts (Wake-on-LAN) und manuelles Anstoßen eines Worker-Taktes direkt aus der HA-App.

---

## 1. MQTT-Broker einrichten (Mosquitto Add-on)

1. Im Home Assistant unter **Einstellungen -> Add-ons** das offizielle **Mosquitto broker** Add-on installieren und starten.
2. Einen Benutzer anlegen:
   * **Einstellungen -> Personen -> Benutzer**
   * Benutzer: `LLM`
   * Passwort vergeben (z. B. `Subaru_277` oder ein eigenes sicheres Passwort).
3. In der Sandbox `mosquitto-clients` installieren und den Service `sandbox/systemd/qwen-mqtt.service` mit der IP deiner Home Assistant Instanz aktivieren.

---

## 2. Sensoren konfigurieren

Kopiere den Inhalt aus [`configuration_sensors.yaml`](configuration_sensors.yaml) in deine `configuration.yaml` in Home Assistant und starte Home Assistant neu.

---

## 3. Dashboard-Karte anlegen

Erstelle eine neue Karte auf deinem Dashboard und füge den YAML-Code aus [`dashboard_card.yaml`](dashboard_card.yaml) ein.
