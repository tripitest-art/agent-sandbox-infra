## Was und warum

Closes #

<!-- Pflicht bei Worker-Aufgaben: "Closes #<Issue>". Nur dann gleicht acceptance-guard
     tests/acceptance/test_issue_<N>.py mit dem Issue ab. -->

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

<!-- Was nicht umgesetzt wurde oder anders als beschrieben, und warum. "keine" ist eine Antwort. -->

## Umgesetzt von / Review durch

<!-- z. B. "Gemini Flash (Worker) / Review: Claude Opus" – Review nie vom selben Agenten -->
