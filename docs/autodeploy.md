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
5. `git pull --ff-only origin main`, dann `pytest -q` (Pre-Flight). Rot: `git reset --hard <alter Stand>`,
   Stand als `bad` vermerkt, „⚠ Stephan:“-Zeile, Status `zurueckgerollt`. `install.sh` läuft dann nicht.
6. `bash sandbox/install.sh` (mit `SKIP_TESTS=1`). Scheitert es: `git reset --hard <alter Stand>` und
   `install.sh` noch einmal, damit installierter Stand und Code zusammenpassen; Meldung wie oben.
7. Erfolg: `last_good` = neuer Stand, `bad` leer, Logzeile „Auto-Deploy: alt -> neu ausgerollt“.

Ein Fehler beim Holen oder `git pull` (Netz weg, Verlauf nicht fortschreibbar) ergibt Status `fehler` und
eine „⚠ Stephan:“-Zeile, aber nur einmal je Fehlertext, nicht alle 15 Minuten.

## Zustand und Logs

- Zustand: `/root/agent-sandbox-infra/.deploy-state.json` (`last_good`, `bad`, `last_error`, `dirty_notified`),
  atomar geschrieben, nicht im Repo (nur verfolgte Dateien zählen für „lokale Änderungen“).
- Log: `/root/worker.log`, Zeilen mit `[deploy]`. „⚠ Stephan:“-Zeilen gehen wie alle anderen an Home Assistant.
  Gemeldet wird nie rohe Ausgabe von pytest oder `install.sh`, nur der Exit-Code (Regel 5).
- Sofort anstoßen: `systemctl start agent-sandbox-deploy.service`. Zurücksetzen eines vermerkten
  `bad`-Stands: `.deploy-state.json` löschen.

## Grenzen

- Der Pre-Flight-Test prüft nur dieses Repo, nicht die Dienste nach dem Neustart. Ein `install.sh`, das
  zwar mit Exit 0 endet, aber einen Timer kaputt konfiguriert, erkennt der Lauf nicht.
- Ändert ein PR `autodeploy.py` oder `install.sh` selbst, gilt er wie jeder andere: Merge nur durch Stephan,
  und nach einem Fehlschlag steht der alte Stand wieder da.
- Der sc-digger-Deploy läuft getrennt (sc-digger #241).
