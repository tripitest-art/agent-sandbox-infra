# Auto-Deploy der Sandbox-Infrastruktur (Stufe 3)

Nach Stephans Merge auf `main` zieht CT 112 den neuen Stand selbst. Kein Agent deployt, kein SSH aus
der Sandbox: Ein Timer auf dem Server holt nur, was schon auf `main` liegt (Deploy-Key ohne
Schreibrecht, `AGENTS.md` Regeln 1 und 4). Der Merge bleibt Stephans Entscheidung.

## Einschalten

```bash
cd /root/agent-sandbox-infra && git pull
AUTODEPLOY=1 bash sandbox/install.sh   # merkt sich AUTODEPLOY=1 in /etc/agent-sandbox.conf
```

Ohne `AUTODEPLOY=1` bleibt der Timer `agent-sandbox-deploy.timer` aus. Ausschalten: `AUTODEPLOY=0 bash sandbox/install.sh`.
Die Units liegen in `sandbox/systemd/`; `autodeploy.py` kommt wie alle `sandbox/*.py` nach `/root/`.

## Ablauf eines Laufs

`sandbox/autodeploy.py` (`deploy_once`) läuft alle 15 Minuten (`flock`, nie zwei gleichzeitig):

1. **Wartet**, solange ein Worker-Slot (`/run/worker-slots/slot-*.json` mit Status `running`) oder ein
   Takt (`/run/qwen-worker-w*.lock`, `/run/qwen-review.lock` per `flock` gehalten) aktiv ist: Status `uebersprungen`.
2. **Lokale Änderungen** an verfolgten Dateien im Klon: nichts wird überschrieben, einmal pro Ziel-Commit
   kommt eine „⚠ Stephan:“-Zeile. Nicht verfolgte Dateien (`.deploy-state.json`, `__pycache__`) zählen nicht.
3. `git fetch origin main`, dann Vergleich von `HEAD` mit `origin/main`. Gleich: `unveraendert` (keine Logzeile).
4. Ein Stand, der schon fehlschlug (`bad` im State), wird nicht wiederholt, bis ein neuer Commit auf `main` liegt.
5. `git pull --ff-only origin main`, dann `pytest -q` (Pre-Flight in einer systemd-Sandbox über
   `systemd-run`, Pfade wie `/etc/agent-settings.json`, `/etc/agent-projects.json`, `/root/sc-digger`
   per `InaccessiblePaths` ausgeblendet). Ist die Isolation auf dem Server nicht nutzbar (`systemd-run`
   schlägt beim Probelauf fehl), fällt der Pre-Flight auf den normalen pytest-Aufruf zurück und `main`
   vermerkt dies bei neuem Stand im Log (`Pre-Flight lief ohne Isolation (systemd-run nicht nutzbar)`).
   Rot: `git reset --hard <alter Stand>`, Stand als `bad` vermerkt, „⚠ Stephan:“-Zeile (nennt bis zu fünf
   rote Tests), Status `zurueckgerollt`. `install.sh` läuft dann nicht.
6. `bash sandbox/install.sh` (mit `SKIP_TESTS=1`). Scheitert es: `git reset --hard <alter Stand>` und
   `install.sh` noch einmal, damit installierter Stand und Code zusammenpassen; Meldung wie oben.
7. Erfolg: `last_good` = neuer Stand, `bad` leer, Logzeile „Auto-Deploy: alt -> neu ausgerollt“.

Ein Fehler beim Holen oder `git pull` (Netz weg, Verlauf nicht fortschreibbar) ergibt Status `fehler` und
eine „⚠ Stephan:“-Zeile, aber nur einmal je Fehlertext, nicht alle 15 Minuten.

## Zustand und Logs

- Zustand: `/root/agent-sandbox-infra/.deploy-state.json` (`last_good`, `bad`, `last_error`, `dirty_notified`),
  atomar geschrieben, nicht im Repo (nur verfolgte Dateien zählen für „lokale Änderungen“).
- Log: `/root/worker.log`, Zeilen mit `[deploy]`. „⚠ Stephan:“-Zeilen gehen wie alle anderen über das Dashboard und ntfy an Stephan.
  Gemeldet wird nie rohe Ausgabe von pytest oder `install.sh` (Regel 5), sondern bei Testfehlern nur der Exit-Code und die Testnamen.
- Sofort anstoßen: `systemctl start agent-sandbox-deploy.service`. Zurücksetzen eines vermerkten
  `bad`-Stands: `.deploy-state.json` löschen.
- Das Web-Dashboard (`sandbox/dashboard.py`) liest dieselbe Datei und zeigt `last_good`/`last_good_at`
  als Kachel „Version“ in der Kopfzeile; ein vermerkter `bad`-Stand erscheint zusätzlich als Banner.
  Ohne Auto-Deploy oder vor dessen erstem Lauf bleibt die Kachel leer (kein Fehler).

## Grenzen

- Der Pre-Flight-Test prüft nur dieses Repo, nicht die Dienste nach dem Neustart. Ein `install.sh`, das
  zwar mit Exit 0 endet, aber einen Timer kaputt konfiguriert, erkennt der Lauf nicht.
- Kann `systemd-run` im Container (z. B. unprivilegierter LXC) keine Mount-Sandbox anlegen, läuft der
  Pre-Flight ohne Isolation; Tests können dann wieder echte Serverdateien berühren, falls diese nicht
  über `tests/conftest.py` abgefangen sind.
- Ändert ein PR `autodeploy.py` oder `install.sh` selbst, gilt er wie jeder andere: Merge nur durch Stephan,
  und nach einem Fehlschlag steht der alte Stand wieder da.
- Der sc-digger-Deploy läuft getrennt (sc-digger #241).
