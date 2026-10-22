## Was und warum

Closes #

<!-- Mandatory for worker tasks: "Closes #<Issue>". Only then does acceptance-guard
     compare tests/acceptance/test_issue_<N>.py against the issue.
     Follow with a concise summary explaining what changed and why. -->

## Wie getestet

- [ ] `python -m pytest -q` grün
- [ ] Akzeptanztests aus dem Issue zeichengenau in `tests/acceptance/test_issue_<N>.py` (falls vorhanden)
- [ ] Neue Logik / behobener Fehler hat einen eigenen Test

## Checkliste (AGENTS.md)

- [ ] Ein Thema, nur die im Issue genannten Dateien
- [ ] Keine Akzeptanztests geändert, keine neuen `skip`/`xfail`, nichts unter `.github/` (sonst begründen, Stephan entscheidet)
- [ ] Keine Zugangsdaten in Code, Logs oder Fehlermeldungen (das Repo ist öffentlich)
- [ ] Doku angepasst (README / AGENTS.md / ROADMAP), falls sich Verhalten oder Betrieb ändert
- [ ] Neue Datei unter `sandbox/`, die ein Takt-Skript importiert: in `install.sh` mitkopiert

## Offene Punkte / Abweichungen vom Issue

<!-- What was not implemented or differs from the issue description, and why. "keine" is a valid answer. -->

## Umgesetzt von / Review durch

<!-- e.g. "Worker: Gemini Flash / Review: Claude Opus" – Reviewer must never belong to the same model family -->
