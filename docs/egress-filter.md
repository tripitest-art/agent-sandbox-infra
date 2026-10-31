# Egress-Filter der Sandbox (CT 112)

Diese Seite hält den **geplanten Ablauf und die belegten Entscheidungen** für
den Egress-Filter von `CT 112` fest. Sie ist **keine freigegebene Firewall-
Konfiguration und kein Runbook zum Anwenden von Regeln**.

## Status

Issue #87
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

## Belegte ausgehende Ziele von CT 112

Dieser Abschnitt beantwortet den offenen Punkt „vollständige Namensliste“ **so weit,
wie der Code es hergibt**. Er ersetzt die passive Mitschnitt-Phase aus #87 nicht: Er
zeigt, was der Code *anfordert*, nicht was auf dem Server *tatsächlich* geschieht.

### Methode und Reichweite

- **Stand:** Commit `ab3251a` auf `main`. Rein statische Lektüre von `sandbox/install.sh`,
  `sandbox/install-lib.sh`, den Python-Modulen unter `sandbox/`, `sandbox/core/`,
  `sandbox/systemd/`, `scripts/` und `proxmox/`. Nichts wurde ausgeführt, nichts
  mitgeschnitten, nichts auf dem Host gelesen.
- **Gesucht:** URLs, Hostnamen, Ports und jeder Netzaufruf (`urllib`, `gh`, `git`, `ssh`,
  `curl`, `apt-get`, `npm`, `pip`). Außer `sandbox/core/benchmark_code.py:70` importiert
  kein Modul unter `sandbox/` `socket`; dort werden Sockets gesperrt (Zeilen 83–85).
- **Pfade** stehen relativ zu `sandbox/` (`install.sh:25` = `sandbox/install.sh`, Zeile 25);
  `core/…` heißt `sandbox/core/…` (nach `/root/core` installiert, `install.sh:317-321`).
  Ein Top-Level-Ordner `core/` existiert nicht.
- **Requirements-Dateien:** Dieses Repo hat keine (`git ls-files` findet keine). `install.sh:139-141`
  installiert die `requirements.txt` der *Projekte* aus der Projektliste; sie liegen in
  deren Repos und sind hier nicht einsehbar.
- **Belegart:** **B** = Ziel steht im Code. **A** = abgeleitet: der Code ruft ein Werkzeug
  (`gh`, `apt`, `npm`, `pip`) auf, das Ziel ist dessen Standard und steht nicht im Repo.
  **K** = Konfiguration: der Wert kommt zur Laufzeit aus `/etc/*.env`, im Repo stehen nur
  Variablenname oder Beispiel.
- **Nicht gezählt:** `.github/` (läuft auf GitHub-Runnern, nicht auf CT 112; dort steht
  `api.github.com` in `.github/scripts/acceptance_guard.py:335-336`), `scripts/` (Planer-Umgebung
  CT 106 laut `scripts/ws.py:1`; `issuecheck.py` startet nur `pytest`, Zeile 381),
  `sandbox/systemd/ollama-override.conf` (Gaming-PC), vom Skript `proxmox/host-create.sh`
  alles außer den Zeilen 26–27 (laufen auf dem Host, nicht im Container).
- **Adressen:** Die Standardwerte für LiteLLM und Ollama sind LAN-Adressen im Code
  (`install.sh:25`, `install.sh:30`). Sie werden hier nicht wiederholt, sondern als
  Platzhalter geführt.

### LAN-Ziele (unverschlüsseltes HTTP)

| Ziel | Port | Belege | Wann | Art |
| --- | --- | --- | --- | --- |
| LiteLLM `<LITELLM-IP>` | 4000 | Standard `install.sh:25`, `worker_tick.py:95`, `review_tick.py:81`, `dashboard.py:91`; Unit-Variable `install.sh:334`, `361`, `433`, `522`; Aufrufe `worker_tick.py:2930` (`/health/liveliness`), `3068-3072` (`/key/info`), `3147-3152` (`/v1/chat/completions`), `review_tick.py:693-697`, `dashboard.py:461-464` (`/key/info`), `core/litellm_client.py:42-49` und `70-75`, `core/benchmark_providers.py:147-154`; Agent selbst: `install.sh:292` (`baseUrl`), `install-lib.sh:84`; Prüfung `install.sh:635-636` (`/v1/models`) | Dauerbetrieb | B |
| LiteLLM, Schreibpfad | 4000 | `dashboard.py:495-501` (`POST /key/update` mit Master-Key, nur mit Schreib-Token) | Dashboard, nur bei Bedienung | B |
| Ollama / WoL-Proxy `<OLLAMA-IP>` | 11434 | Standard `install.sh:30`, `worker_tick.py:87`; Aufrufe `worker_tick.py:2903` (`/proxy/status`), `4124` (`/api/ps`); Eintrag in den Agenten-Konfigurationen `install.sh:278`, `install-lib.sh:78`; Prüfung `install.sh:639` (`/api/tags`) | **Nur** bei `BACKEND=ollama` (Standard in `install.sh:24` ist `litellm`; `worker_tick.py:4353-4364` fragt den PC nur im Ollama-Zweig) | B |
| DNS-Resolver | 53 (UDP/TCP) | Im Code nicht gesetzt; der Container bezieht Netzwerk per `ip=dhcp` (`proxmox/host-create.sh:22`) | Immer, für jeden Namen unten | A |

Zum Ollama-Eintrag: Das Aufwecken des Gaming-PCs sendet laut `worker_tick.py:2901` und
`4121` der WoL-Proxy, nicht CT 112. Magic-Packet-Code gibt es im Repo nicht; CT 112
spricht den Proxy nur per HTTP an.

### Ziele im Internet (TCP 443, wo nicht anders genannt)

| Name | Zweck | Belege | Wann | Art |
| --- | --- | --- | --- | --- |
| `github.com` | Klonen, `git fetch`, `git push -u origin` der Projekt-Klone (Remote = Klon-URL) | `install-lib.sh:145` (Klon, Host fest, Repo nur `owner/name`: `projects.py:19`, `64`), `install-lib.sh:117`, `worker_tick.py:1652`, `1711`, `3876`, `4267`, `4281`, Push `1884`; einmalig beim Anlegen des Containers `proxmox/host-create.sh:27` | Dauerbetrieb | B |
| `github.com`, **Port 22 (SSH)** | Deploy-Key des Infra-Klons: `git fetch`/`git pull` im Auto-Deploy | `install.sh:604-611` (`Host github-infra` → `HostName github.com`, `User git`), `616` (`ssh -T`), `618` (`remote set-url`), Verbraucher `autodeploy.py:192`, `265`, `288` (Arbeitsordner `autodeploy.py:28`, `346-351`) | Nur, wenn der Deploy-Key bei GitHub eingetragen ist (`install.sh:617-625`) | B |
| `api.github.com` | Alle `gh`-Aufrufe (Issues, PRs, Labels, Checks; GraphQL, siehe `install.sh:111`) | `core/gh_client.py:35-36`, `dashboard.py:454`, `install.sh:631` (`gh auth status`); der Hostname selbst steht im CT-112-Code nicht | Dauerbetrieb | A |
| `cli.github.com` | apt-Quelle und Schlüssel für `gh` | `install.sh:114`, `117`; jeder spätere `apt-get update` (`install.sh:107`, `119`) | Bei jedem `install.sh`-Lauf | B |
| `deb.nodesource.com` | Installationsskript für Node 22 | `install.sh:254-256` | Nur wenn Node < 22 | B |
| `opencode.ai` | Installationsskript (`curl … \| bash`) | `install.sh:245` | Nur wenn `/root/.opencode/bin/opencode` fehlt, **unabhängig von `AGENT`** | B |
| npm-Registry | `@qwen-code/qwen-code@0.24.7`, optional `context-mode` | `install.sh:60`, `258-259`, `261` (nur `CONTEXT_MODE=1`); keine `.npmrc` im Repo | Nur wenn Version abweicht | A |
| PyPI (Index und Dateiserver) | `pip install -r requirements.txt` der Projekte, `pip install pytest` | `install.sh:140`, `142`; keine `pip.conf` und kein `--index-url` im Repo | Bei jedem `install.sh`-Lauf | A |
| Debian-Paketspiegel | `git curl ca-certificates python3 … ffmpeg … tmux logrotate`, `gh`, `nodejs` | `install.sh:107-109`, `119-120`, `256`; `proxmox/host-create.sh:26` (einmalig); Spiegelname steht nicht im Repo (Vorlage `debian-12-standard`, `host-create.sh:15`) | Bei jedem `install.sh`-Lauf | A |
| ntfy-Server, **Name unbekannt** | Wake-Strom (GET, dauerhaft offen), Push (POST), Wake-Auslöser (POST) | `WAKE_URL`: `install.sh:242`, `485`, `wake_listener.py:30-46`, `106-107`; `NTFY_URL`: `install.sh:508`, `dashboard_notify.py:62-63`, `77-80`; `WAKE_PUBLISH_URL`: `dashboard.py:572`, `591-593`. Als Beispiel nur `ntfy.sh` (`install.sh:508`, `dashboard_notify.py:12`, `README.md:36`) | Dauerbetrieb, falls gesetzt | K |
| Benchmark-Anbieter (Beispiele) | `api.groq.com`, `api.cerebras.ai`, `generativelanguage.googleapis.com` | **Nur** `benchmark_providers.example.json:11`, `18`, `25`; echte Liste unter `/etc/agent-benchmark-providers.json` (`benchmark.py:222-225`, `core/benchmark_providers.py:20`). Die Timer-Units liegen unter `systemd/agent-benchmark.*`, `install.sh` ruft `benchmark_units_install` (`install-lib.sh:203`) aber nicht auf | Unklar, ob aktiv | K |

Zu `ntfy`: `wake_listener.py:42` und `dashboard_notify.py:63` akzeptieren `http://` und
`https://`, Port und Schema sind also nicht festgelegt. `NTFY_URL`, `WAKE_URL` und
`WAKE_PUBLISH_URL` können auf denselben oder auf verschiedene Server zeigen.

### In #87 genannt, im Code aber nicht zu finden

| Ziel aus #87 | Befund |
| --- | --- |
| SearXNG (`CT 110`) | Kein Treffer für „searx“ im ganzen Repo außer in dieser Doku. Wie die Agenten die Suche erreichen, ist aus dem Code nicht ableitbar. OpenCode hat `webfetch: deny` (`install-lib.sh:89`); für Qwen Code legt `install.sh:269-308` keine Websuche oder Webabruf-Einstellung fest. |
| AdGuard (`CT 101`) | Kein Treffer. Der Resolver wird nirgends gesetzt (siehe DNS oben). |
| Home Assistant, Port 8123 | Kein Code in diesem Repo spricht Home Assistant an. `HA_STATUS_URL` und `HA_WEBHOOK_URL` kommen nur als zu schützende Namen vor (`worker_tick.py:374-376`). `ROADMAP.md:225` führt `ha_status.py` und `qwen_webhook.py` als stillgelegt; beide Dateien liegen nicht im Repo. Kommentare wie `review_tick.py:25` und `197-198` beschreiben noch einen Webhook, ein Sender ist nicht im Repo. Älter: `docs/betriebsbericht-2026-10-15.md:17` nennt einen aktiven „Webhook-Dienst“. |
| Caddy (`CT 103`), Tailscale | Kein Treffer; beides ist in #87 ohnehin als nicht erlaubt bzw. bedeutungslos festgelegt. |
| Forward-Proxy | Kein Treffer: `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` kommen im Repo nicht vor. |

### Befunde, die #87 noch nicht berücksichtigt

1. **Ollama fehlt in der Zieltabelle von #87.** Gilt nur bei `BACKEND=ollama`. Welches
   Backend auf dem Server läuft, ist hier nicht geprüft; `BACKEND` gehört nicht zu den
   gemerkten Einstellungen (`install.sh:100`), ein Lauf ohne Angabe fällt auf `litellm` zurück.
2. **SSH (Port 22) zu GitHub ist über einen CONNECT-Proxy auf 443 nicht erreichbar.** Der
   Auto-Deploy holt `origin/main` des Infra-Klons über diesen Weg. Fällt er aus, bleibt laut
   `install.sh:624` nur HTTPS; ob das für das Repo ohne Zugangsdaten funktioniert, ist
   nicht belegt (`install.sh:591` nennt das Repo privat, `AGENTS.md` öffentlich).
3. **Proxy-Variablen erreichen den Agentenprozess nicht.** `agent_env` reicht nur
   `AGENT_ENV_ALLOW` und `AGENT_ENV_PASS` durch (`worker_tick.py:381-383`, `391`, `394-403`);
   `HTTPS_PROXY`/`NO_PROXY` stehen nicht darin. Der Takt selbst (und damit `gh`, `git push`)
   bekommt die volle Umgebung (`worker_tick.py:371`). Die Units setzen keine Proxy-Variablen
   (`install.sh:326-350`, `425-444`, `475-492`, `512-534`). Eine Änderung an `worker_tick.py`
   ist ein Engpass (Regel 8) und braucht ein eigenes Issue.
4. **`apt` nutzt je nach Quelle `http` (Port 80).** Der Spiegel steht nicht im Repo; ein
   Proxy mit nur `CONNECT` auf 443 deckt das eventuell nicht ab. Der Mitschnitt klärt es.
5. **LiteLLM und Ollama sind `http://`.** Beides geht nie über den Forward-Proxy, sondern
   nur über Firewall-Regeln (wie in #87 vorgesehen).
6. **Install-Namen sind kein reiner Einrichtungsfall.** `install.sh` läuft auch aus dem
   Auto-Deploy (`autodeploy.py:5`, alle 15 Minuten, `systemd/agent-sandbox-deploy.timer:6-7`,
   nur bei `AUTODEPLOY=1`). Firewall und Proxy unterscheiden nicht zwischen dem Takt als `root`
   und dem Agentennutzer `worker` (`install.sh:151`): Jedes erlaubte Ziel steht beiden offen.
7. **Wake-Strom ist eine Dauerverbindung.** `wake_listener.py:107` öffnet sie mit 60 s
   Lese-Timeout; ein Proxy darf Leerlauf nicht früher trennen.

## Regeln (abgeleitet, Entwurf)

**Nicht freigegeben.** Die folgenden Regeln leiten nur ab, was die Belege oben tragen. Sie
sind weder gemessen noch geprüft und enthalten absichtlich weder Proxmox-Syntax noch
Adressen (Platzhalter in `<…>`). Bei Konflikt gilt die passive Messung aus #87.

### Firewall (Richtung OUT, Standard `DROP`, Verwerfen protokollieren)

| Nr. | Ziel | Port | Bedingung | Beleg |
| --- | --- | --- | --- | --- |
| F1 | `<ADGUARD-IP>` | 53 UDP und TCP | immer; vorher prüfen, dass der Container wirklich nur diesen Resolver nutzt | DNS-Zeile oben, #87 |
| F2 | `<LITELLM-IP>` | 4000 TCP | immer | LiteLLM-Zeile |
| F3 | `<PROXY-IP>` | `<PROXY-PORT>` TCP | sobald der Proxy steht; der Port ist im Repo nicht festgelegt | #87 |
| F4 | `<OLLAMA-IP>` | 11434 TCP | **nur** wenn `BACKEND=ollama` gewollt ist; sonst weglassen | Ollama-Zeile, Befund 1 |
| F5 | `<NTFY-IP>` | `<NTFY-PORT>` TCP | **nur** bei eigenem ntfy-Server; bei `ntfy.sh` stattdessen Proxy | ntfy-Zeile |
| F6 | `<SEARXNG-IP>` | `<SEARXNG-PORT>` TCP | aus dem Code nicht ableitbar; laut #87 Pflicht, Port von Stephan | #87 |
| F7 | GitHub (Port 22) | 22 TCP | **offene Entscheidung** (Befund 2): nur als Adressliste möglich, nicht als Name; nur nötig bei aktivem Deploy-Key und Auto-Deploy | SSH-Zeile |
| F8 | alles andere, auch das übrige LAN | – | `DROP` und protokollieren | #87 |

Eingehend (ebenfalls aus dem Code belegt, Quelladressen nicht hier): SSH 22 von Stephans
Rechner (`proxmox/host-create.sh:30`) und `DASHBOARD_PORT`, Standard 8099
(`install.sh:72`, `521`, `538`), aus dem LAN. Antworten auf bestehende Verbindungen
gehören dazu.

### Forward-Proxy (nur `CONNECT` auf 443, Quelle nur CT 112)

| Name | Zweck | Stand |
| --- | --- | --- |
| `github.com` | Klonen, Fetch, Push | belegt (B) |
| `api.github.com` | `gh` | abgeleitet (A) |
| `cli.github.com` | `gh`-Paketquelle | belegt (B) |
| `deb.nodesource.com` | Node 22 | belegt (B), nur bei Bedarf |
| `opencode.ai` | OpenCode-Installer | belegt (B), nur bei Bedarf |
| `registry.npmjs.org` | Qwen Code, `context-mode` | abgeleitet (A), Name nicht im Repo |
| `pypi.org`, `files.pythonhosted.org` | `pip` | abgeleitet (A), Namen nicht im Repo |
| Debian-Spiegel | `apt` | **offen**, Name und Schema unbekannt |
| `ntfy.sh` | Push und Wake-Strom | **offen**, nur Beispiel im Code (K) |

Die Namen `registry.npmjs.org`, `pypi.org` und `files.pythonhosted.org` stammen aus dem
Standardverhalten von `npm` und `pip`, nicht aus diesem Repo; der Mitschnitt muss sie
bestätigen. Nicht auf die Liste: die Benchmark-Beispielanbieter, solange keine echte
Providerliste vorliegt.

### Restunsicherheit

Ausdrücklich **nicht** belegt:

1. Welche Spiegel `apt` tatsächlich nutzt und ob `http` oder `https`.
2. Welcher ntfy-Server hinter `WAKE_URL`, `NTFY_URL`, `WAKE_PUBLISH_URL` steht (`ntfy.sh` ist
   nur ein Beispiel im Code) und wo `WAKE_PUBLISH_URL` gesetzt wird (`install.sh` fragt es nicht ab).
3. Weitere Hosts, die `gh`, `git`, `npm`, `pip`, der OpenCode-Installer oder `apt`
   selbst ansprechen (Weiterleitungen, Download-Server). `opencode.json` trägt außerdem
   `https://opencode.ai/config.json` als `$schema` (`install-lib.sh:73`) und das npm-Paket
   `@ai-sdk/openai-compatible` (`install-lib.sh:76`, `82`); ob OpenCode das zur Laufzeit lädt, ist
   aus dem Code nicht erkennbar.
4. Wie `git push` und `gh` sich anmelden: `install.sh:631` erwähnt `/usr/local/bin/gh-app-token`,
   das nicht im Repo liegt. Seine Ziele sind unbekannt.
5. Wie die Agenten SearXNG erreichen und ob Qwen Code eigene Web-Werkzeuge nutzt. Der
   Code konfiguriert dazu nichts. `install.sh:306-307` schaltet nur Nutzungsstatistik und
   Telemetrie aus.
6. Ob auf CT 112 außerhalb des Repos noch ein Home-Assistant-Sender läuft.
7. Welches Backend (`litellm` oder `ollama`) und welche Timer (Auto-Deploy, Benchmark)
   auf dem Server tatsächlich aktiv sind.
8. Netzverhalten des Projektcodes: `requirements.txt` und Tests der Projekte
   (`install.sh:139-141`, `648`) liegen in anderen Repos und können eigene Ziele haben.
9. Dynamische Ziele (Adressen, die erst zur Laufzeit aus Daten entstehen) lassen sich
   statisch nicht vollständig erfassen.

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
- Die Namensliste für GitHub, Paketquellen und ggf. ntfy ist aus dem Code abgeleitet
  (Abschnitt „Belegte ausgehende Ziele“), aber **nicht vollständig** und nicht
  gemessen; die Lücken stehen unter „Restunsicherheit“ und müssen aus der
  Mitschnitt-Phase hervorgehen. Namen wie `api.github.com`, PyPI-Hosts oder `ntfy.sh`
  sind nicht allein durch diese Doku als freigegebene Ziele bestätigt.
- Ob `firewall=1` an der Netzwerkkarte genügt bzw. die Container-Firewall
  (`/etc/pve/firewall/112.fw`) aktiv ist, war laut #87 nicht geprüft.
- Die HAOS-Ausnahme gilt nur bis zum beschlossenen Home-Assistant-Rückbau; der
  tatsächliche Abschluss ist hier nicht belegt.
- Keine IP-Adressen, Zugangsdaten, Schlüssel oder Webhook-IDs eintragen.
- Host-Konfiguration und Installation bleiben Stephans Aufgabe. Diese Seite
  ändert weder Proxmox noch `install.sh`, `worker_tick.py` oder `review_tick.py`.
