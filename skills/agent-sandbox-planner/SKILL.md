---
name: agent-sandbox-planner
description: "Worker-Issue für agent-sandbox-infra planen und prüfen (Planer-Rolle). Nutzen bei „Plane …“, „Erstelle ein Issue für …“ oder „Mach daraus eine Aufgabe“. Erstellt vertragskonforme Worker-Issues, führt Pre-Flight mit scripts/issuecheck.py durch und legt nach OK das Issue an."
always-apply: true
---

# Planer-Skill: agent-sandbox-infra

Für jeden Planer-Agenten, gleich welches Modell. Die Grundregeln stehen in `AGENTS.md` (Abschnitt „Worker-Aufgaben → Planer“)
und der genaue Ablauf in `docs/planer-ablauf.md`.

Der Planer schreibt Issues, keinen Code: keine Branches, keine Commits, keine PRs, kein Merge.

---

## Ablauf in 8 Schritten

### Schritt 1: Auftrag klären
- Ziel in ein bis zwei Sätzen klären.
- Nicht-Ziele („Nicht Teil dieser Aufgabe“) festhalten.
- Nur ein Thema pro Issue; größere Aufgaben in handhabbare Einheiten schneiden („Teil 1 von #X“).
- Offene Fachfragen Stephan stellen, nicht raten.

### Schritt 2: Stand und Konflikte prüfen (Regel 8)
- Offene Issues und PRs nach Überschneidungen durchsuchen:
  - `gh pr list --repo tripitest-art/agent-sandbox-infra --state open`
  - `gh issue list --repo tripitest-art/agent-sandbox-infra --state open --label in-arbeit`
- Ändert ein offener PR dieselben Dateien: Label `blockiert` und Zeile `Wartet auf: #X` im Kontext ergänzen.
- **Achtung vor Blockadefalle (#46):** Keine Zeile `Wartet auf: #<Zahl>` innerhalb des Akzeptanztest-Blocks oder als Beispiel notieren, da der Review-Takt diese Zeilen als echte Abhängigkeit liest.

### Schritt 3: Code lesen
- Jede Datei aus dem geplanten Issue auf dem aktuellen Stand von `main` lesen.
- Bestehende Helfer und Testmuster aus `tests/test_worker_tick.py`, `tests/test_acceptance_guard.py` oder `tests/test_projects.py` wiederverwenden.
- Bestehende Akzeptanztests prüfen (`git grep <funktion> tests/acceptance`): Worker dürfen sie nicht abschwächen oder brechen.

### Schritt 4: Issue-Text strukturieren
Der Issue-Text muss exakt dem Formular `.github/ISSUE_TEMPLATE/worker-task.yml` entsprechen:
1. `### Ziel`
2. `### Betroffene Dateien` (vollständig; immer dabei: `tests/acceptance/test_issue_<N>.py` und die eigene Testdatei)
3. `### Schnittstellen` (in genau einem ```` ```python ````-Block mit exakten Typen und Signaturen)
4. `### Nicht Teil dieser Aufgabe`
5. `### Akzeptanztests` (in genau einem ```` ```python ````-Block direkt darunter)
6. `### Fertig, wenn`
7. `### Merge-Modus` (`automatisch nach Review` als Standard; `manuell durch Stephan (Betrieb, Server, Zugangsdaten)` bei Aufsichtsdateien)
8. `### Kontext`

### Schritt 5: Pre-Flight-Prüfung (`scripts/issuecheck.py`)
Entwurf lokal als Datei speichern (z. B. `entwuerfe/issue-<thema>.md`) und mit Shell prüfen:

```bash
python scripts/issuecheck.py entwuerfe/issue-<thema>.md
```

- Prüft Pflichtüberschriften, Merge-Modus und Pfadvorgaben.
- Prüft AST auf verbotene Importe (Netzwerk, Zufall), Skip-Marker (`skip`/`xfail`) und absolute `/root`-Pfade.
- Führt den Akzeptanztest mit pytest isoliert gegen das Repository aus.
- **Erwartung:** Vor der Umsetzung muss der Test rot sein (Exit Code != 0 / FAIL / `ImportError` / `AssertionError`).
- Erst wenn `scripts/issuecheck.py` mit `Akzeptanztests sind valide und bereit für die Sandbox` quittiert, ist der Entwurf fertig.

*Hinweis für Planer ohne Shell:* Kann das Skript mangels Shell nicht ausgeführt werden, erhält das Issue das Label `entwurf` statt `bereit` und einen Hinweis im Kontext („Akzeptanztests noch nicht ausgeführt (Planer ohne Shell)“).

### Schritt 6: Entwurf im Chat vorlegen
Titel, vollständigen Text und Labels Stephan im Chat vorlegen:
„Soll ich das Issue so anlegen?“
Erst nach Stephans OK anlegen.

### Schritt 7: Issue anlegen
Issue via `gh issue create` mit folgenden Labels erstellen:
- `worker-task`
- genau ein `agent-<familie>` (`agent-qwen`, `agent-antigravity`, `agent-claude`)
- `bereit` (oder `entwurf`, falls ohne Shell ungeprüft)
- bei Bedarf `blockiert`

Abschließend Issue-Nummer und Link an Stephan melden.

---

## Vorlage für den Issue-Text

````markdown
### Ziel

<Ein, zwei Sätze: Was soll danach anders sein?>

### Betroffene Dateien

- `sandbox/<datei>.py` (<Funktion>)
- `tests/acceptance/test_issue_<N>.py`: Akzeptanztests unten, zeichengenau
- `tests/test_<thema>.py` (neu): eigene Tests

### Schnittstellen

```python
def <funktion>(<param>: <typ>) -> <rueckgabetyp>:
    """<Verhalten, auch bei Fehlern und Randfällen>"""
```

### Nicht Teil dieser Aufgabe

- keine Änderungen an install.sh oder systemd-Units
- kein Refactoring unbeteiligter Dateien

### Akzeptanztests

```python
import pytest
from sandbox.<datei> import <funktion>


def test_<eigenschaft>():
    assert <funktion>(...) == ...
```

### Fertig, wenn

- [ ] Akzeptanztests grün
- [ ] Weitere Tests decken das neue Verhalten ab (Netzwerk nur über Fakes)
- [ ] `python -m pytest -q` komplett grün

### Merge-Modus

automatisch nach Review

### Kontext

- Anlass / ROADMAP-Stufe / Fehlerbild
- Pre-Flight: mit `python scripts/issuecheck.py` geprüft (rot vor Umsetzung: <Grund>)
````
