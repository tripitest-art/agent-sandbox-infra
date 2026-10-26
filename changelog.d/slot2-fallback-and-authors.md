### Geändert

- Slot 2 fällt bei fehlenden `worker:billig`-Aufgaben auf reguläre `bereit`-Issues zurück, statt im Leerlauf zu verharren.
- Autorenfilter `ALLOWED_ISSUE_AUTHORS` in `worker_tick.py` um `app/hermes-agent-696969` erweitert, damit von Hermes erstellte Aufgaben autonom abgearbeitet werden dürfen.
