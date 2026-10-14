# Arbeiten über MCP (ohne Shell)

Für Agenten, die keinen Shell-Zugang haben: dieselben Regeln wie in [`AGENTS.md`](../AGENTS.md),
aber mit den GitHub-Werkzeugen des Agenten statt mit `gh`. Diese Datei ist aus `MCP.md` in
`tripitest-art/sc-digger` übernommen und auf dieses Repository angepasst.

## Befehle → MCP-Werkzeuge

Repo immer `owner: tripitest-art`; `repo` ist das Projekt, an dem du arbeitest — für dieses
Repository `agent-sandbox-infra`.

| In `AGENTS.md` | MCP-Werkzeug |
|---|---|
| Datei lesen | `get_file_contents` (`ref`: `main` oder dein Branch) |
| `gh issue view <N>` | `issue_read` (`method: get`, für Kommentare `get_comments`) |
| `gh issue list …` | `search_issues`, z. B. `repo:tripitest-art/agent-sandbox-infra is:issue is:open label:worker-task label:bereit -label:blockiert`, sortiert nach `created` aufsteigend |
| `gh issue edit --add-label/--remove-label` | `issue_write` (`method: update`, `labels`: **vollständige** Liste) |
| `gh issue comment` | `add_issue_comment` |
| `git checkout -b <branch>` | `create_branch` (`from_branch: main`) |
| `git commit` + `git push` | `push_files` (mehrere Dateien, ein Commit) |
| `gh pr create` | `create_pull_request` (`base: main`, Body nach `.github/pull_request_template.md`, mit `Closes #<N>`) |
| `gh pr edit` (PR-Text, Titel) | `update_pull_request` |
| `gh pr view --comments` | `pull_request_read` (`get`, `get_comments`, `get_review_comments`) |
| `gh pr diff` | `pull_request_read` (`get_diff`, bei großen PRs `get_files`) |
| `gh pr checks` | `pull_request_read` (`get_check_runs`) |
| `gh pr view --json reviews,commits` | `pull_request_read` (`get_reviews`, `get_commits`) |
| `gh pr review --comment` | `pull_request_review_write` (`method: create`, `event: COMMENT`, `body`) |
| `gh pr merge --squash` | `merge_pull_request` (`merge_method: squash`) — in diesem Repository **nie**: hier mergt Stephan (Regel 4) |
| `gh pr list …` | `list_pull_requests` / `search_pull_requests` |
| CI-Log bei rotem Check | `get_job_logs` |

## Regeln, die nur über MCP gelten

- **`push_files` schreibt ganze Dateien.** Immer den vollständigen Inhalt schicken, vorher mit
  `get_file_contents` vom eigenen Branch lesen. Nie „…" oder „Rest unverändert": Was fehlt, ist
  danach gelöscht.
- **Akzeptanztest zeichengenau:** Den Block aus dem Issue-Text kopieren, nicht neu tippen oder
  umformatieren. Der erste Commit auf dem Branch enthält nur `tests/acceptance/test_issue_<N>.py`.
- **Labels:** `issue_write` ersetzt die ganze Label-Liste. Erst mit `issue_read` alle Labels
  lesen, nur `bereit` durch `in-arbeit` ersetzen, die vollständige Liste zurückschreiben. Titel
  und Text nie mitschicken. `freigabe-geschützt` setzt nur Stephan.
- **Nur ein Agent je Engpassdatei** (Regel 8): `sandbox/worker_tick.py`, `sandbox/review_tick.py`
  und `sandbox/install.sh`. Vor der Übernahme prüfen, ob ein offenes Issue mit `in-arbeit` dieselbe
  Datei nennt — hier gibt es keinen Hotspot-Schlüssel in der Projektliste, die Prüfung läuft über
  die Dateiliste im Issue.
- **Nach jedem `push_files` prüfen:** `pull_request_read` (`get_files`) zeigt, ob in einer Datei
  mehr gelöscht als geändert wurde. Dann ist sie abgeschnitten: sofort mit dem vollständigen
  Inhalt neu schreiben.
- **Tests:** Ohne Shell zählt der CI-Check `tests`. Fertig erst, wenn `tests` und
  `acceptance-guard` grün sind. Änderungen an `.github/`, `conftest.py` oder der
  pytest-Konfiguration brauchen `freigabe-geschützt` (Stephan) — sie gehören in ein eigenes Issue.
- **Kontext knapp?** Lieber aufhören und im Issue kommentieren als raten (Worker, Schritt 6).
