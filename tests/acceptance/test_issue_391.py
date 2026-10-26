import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
ISSUECHECK = ROOT / "scripts" / "issuecheck.py"

def test_issuecheck_skript_existiert_und_ausfuehrbar():
    assert ISSUECHECK.exists(), "scripts/issuecheck.py fehlt"

def test_issuecheck_erkennt_fehlende_abschnitte(tmp_path):
    bad_issue = tmp_path / "bad_issue.md"
    bad_issue.write_text("# Titel\nNur Text ohne Pflichtabschnitte", encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(ISSUECHECK), str(bad_issue)],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "Pflichtabschnitt" in res.stdout or "Pflichtabschnitt" in res.stderr or "Fehler" in res.stdout

def test_issuecheck_erkennt_verbotene_imports(tmp_path):
    bad_issue = tmp_path / "bad_import.md"
    bad_issue.write_text("""# Test
## Kontext
K
## Betroffene Dateien
- datei.py
## Anforderungen
A
## Akzeptanztest
```python
import requests
def test_x(): pass
```
## Fertig wenn
F
""", encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(ISSUECHECK), str(bad_issue)],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "requests" in res.stdout or "Verboten" in res.stdout
