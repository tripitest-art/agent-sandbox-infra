## Was und warum

Closes #

<!-- Pflicht bei Worker-Aufgaben: "Closes #<Issue>". Nur dann gleicht acceptance-guard
     tests/acceptance/test_issue_<N>.py mit dem Issue ab. -->

## Wie getestet

- [ ] `python -m pytest -q` grün
- [ ] Akzeptanztests aus dem Issue zeichengenau in `tests/acceptance/test_issue_<N>.py` (falls vorhanden)
- [ ] Neue Logik / behobener Fehler hat einen eigenen Test
- [ ] Bei Änderungen am Lauf: `--dry-run --no-telegram -v` einmal ausgeführt

## Checkliste (AGENTS.md)

- [ ] Ein Thema, nur die im Issue genannten Dateien
- [ ] Keine Akzeptanztests geändert, keine neuen `skip`/`xfail`, nichts unter `.github/` (sonst begründen, Stephan entscheidet)
- [ ] Sammlung (`/music/Schranz`) wird nicht verändert
- [ ] Keine Zugangsdaten in Code, Logs oder Fehlermeldungen (Telegram nur über `telegram_call`)
- [ ] `config.yaml`: neue Optionen haben einen Standardwert und einen Kommentar
- [ ] Doku angepasst (README / AGENTS.md / ROADMAP), falls sich Verhalten oder Betrieb ändert

## Offene Punkte / Abweichungen vom Issue

<!-- Was nicht umgesetzt wurde oder anders als beschrieben, und warum. "keine" ist eine Antwort. -->

## Umgesetzt von / Review durch

<!-- z. B. "Gemini Flash (Worker) / Review: Claude Opus" – Review nie vom selben Agenten -->
