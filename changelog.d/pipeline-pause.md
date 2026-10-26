### feat(dashboard): Pipeline-Pause nach laufenden Tasks steuern

* Neuer Umschalter `⏸ Nach nächstem Task stoppen` / `▶ Pipeline fortsetzen` im Header des Dashboards (`sandbox/dashboard.py`).
* API-Endpunkt `POST /api/pause` mit Schreib-Token-Schutz zum Erstellen/Löschen von `/run/pipeline-pause`.
* Status-Anzeige und Warnbanner im Dashboard bei aktiver Pause.
* `sandbox/worker_tick.py`: `pipeline_pausiert()` sperrt die Annahme neuer Issue-Aufträge, während laufende Jobs und Nacharbeiten sauber zu Ende laufen.
