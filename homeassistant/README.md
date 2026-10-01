# 🏠 Home Assistant Integration & Dashboard

Dieses Modul bindet die Proxmox-Sandbox und den LLM-Host in Home Assistant ein:
1. **Hardware-Telemetrie:** Überwachung von GPU-Verbrauch (Watt), VRAM (GB), Temperaturen und Status des Gaming-PCs (Schläft, Fährt hoch, Ollama Online) über den WoL-Proxy.
2. **Live-Log-Streaming:** Echtzeit-Stream der Arbeitsschritte des Coding-Agenten per **HA-Webhook**.
3. **Fernsteuerung:** Manuelles Wecken des LLM-Hosts (Wake-on-LAN) und manuelles Anstoßen eines Worker-Taktes direkt aus der HA-App.

---

## 1. Live-Log per Webhook (nicht mehr MQTT)

Bis 01.10.2026 lief das Log über MQTT. Das wurde ersetzt: Mit dem MQTT-Login konnte die Sandbox
am Broker **alles** lesen und schreiben (auch Zendure, Frigate, Zigbee2MQTT), und die ACL der
Mosquitto-App greift nicht (das Auth-Plugin erlaubt vorher). Ein Webhook bedient dagegen nur
einen einzigen Auslöser.

1. Webhook-ID erzeugen (HA-Terminal) und in `secrets.yaml` eintragen – **nie in dieses Repo**:
   ```bash
   openssl rand -hex 24
   ```
   `secrets.yaml`: `qwen_worker_webhook_id: "<wert>"`
2. Den Template-Sensor aus [`configuration_sensors.yaml`](configuration_sensors.yaml) (Abschnitt 2)
   übernehmen, dann `template.reload`. `local_only: true` lässt nur Aufrufe aus dem Heimnetz zu.
3. In der Sandbox `bash sandbox/install.sh` ausführen: Es installiert `qwen-webhook.service`,
   fragt `HA_WEBHOOK_URL` verdeckt ab (`http://<HA-IP>:8123/api/webhook/<wert>`, gespeichert nur in
   `/etc/qwen-webhook.env`, Rechte 600) und entfernt den alten `qwen-mqtt.service` samt Passwort.
4. Danach den MQTT-Login `LLM` in der Mosquitto-App (`logins`) löschen und die App neu starten.
   Die Firewall der Sandbox braucht dann nur noch HA-Port 8123, nicht mehr 1883.

---

## 2. Sensoren konfigurieren

Kopiere den Inhalt aus [`configuration_sensors.yaml`](configuration_sensors.yaml) in deine `configuration.yaml` in Home Assistant und lade die Template- bzw. REST-Integration neu.

---

## 3. Dashboard-Karte anlegen

Erstelle eine neue Karte auf deinem Dashboard und füge den YAML-Code aus [`dashboard_card.yaml`](dashboard_card.yaml) ein.
