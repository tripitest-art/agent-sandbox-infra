# Entwurf B: Autodeploy der Takt-Skripte mit Rollback

Titel: `autodeploy_tick.py: Takt-Skripte nach grünem CI auf main selbst ausrollen, mit Rollback`
Labels: `worker-task`, `blockiert`, `phase-?`, `agent-?`; Stephan setzt `freigabe-geschützt` auf den PR
(ändert AGENTS.md Regel 4)
Merge-Modus: manuell durch Stephan
Kontext-Zeile: `Wartet auf: #<Nummer von Issue A>` (gleiche Datei install.sh, Regel 8)

## Ziel

Ein Timer auf dem Server holt `origin/main`, und wenn der Stand von `main` grünes CI hat und nur
unkritische Dateien geändert wurden (`sandbox/*.py`, Doku, Tests), kopiert er die Skripte nach `/root/`
(wie `install.sh` es tut), prüft sie und rollt bei Fehlschlag auf den letzten guten Stand zurück.
Änderungen an `install.sh`, `install-lib.sh`, Units, `requirements.txt` oder unbekannten Dateien
rollt er nie selbst aus, sondern meldet sie mit „⚠ Stephan:“; die macht weiter nur Stephan.

## Betroffene Dateien

- sandbox/autodeploy_tick.py (neu)
- sandbox/install.sh (Einrichtung von `agent-autodeploy.service/.timer`, nur bei `AUTODEPLOY=1`, Vorgabe 0, wird gemerkt)
- sandbox/install-lib.sh (nur falls `AUTODEPLOY` in die gemerkten Einstellungen soll)
- AGENTS.md (Regel 4 und Ablauf Schritt 7: Ausnahme „Autodeploy-Timer, nur Takt-Skripte“)
- tests/test_autodeploy_tick.py, README.md, CHANGELOG.md

## Schnittstellen

```python
# sandbox/autodeploy_tick.py  (nur Standardbibliothek; subprocess, gh und Uhr nur in main(), nie auf Modulebene)

def classify_changes(files: list[str]) -> str:
    """'nichts' bei leerer Liste. 'kopieren', wenn jede Datei harmlos ist: sandbox/<name>.py (direkt in
    sandbox/), docs/..., tests/..., .github/..., LICENSE oder eine *.md im Hauptordner.
    Sonst 'manuell' (install.sh, install-lib.sh, sandbox/systemd/..., requirements.txt, Unbekanntes)."""

def decide(local: str, remote: str, ci: str, failed: str | None) -> str:
    """local = zuletzt ausgerollter Commit, remote = origin/main, ci in {'success','pending','failure','unknown'},
    failed = Commit, dessen Rollout zuletzt zurückgerollt wurde (oder None).
    'nichts' wenn local == remote; sonst 'gesperrt' wenn remote == failed oder ci == 'failure';
    sonst 'warten' wenn ci != 'success'; sonst 'deployen'."""

def run_deploy(old: str, new: str, *, changed: list[str], checkout, copy, smoke, notify) -> dict:
    """checkout(sha) setzt den Klon auf sha, copy() kopiert sandbox/*.py nach /root/, smoke() -> bool,
    notify(zeile) schreibt ins Log. Rückgabe {'ergebnis': ..., 'sha': ...}:
      'nichts'          (classify == nichts): kein Aufruf, sha = new
      'manuell'         : kein Aufruf außer notify('⚠ Stephan: …' mit Dateinamen), sha = old
      'ok'              : checkout(new), copy(), smoke() == True, sha = new
      'zurueckgerollt'  : copy() wirft oder smoke() ist False: checkout(old), copy(), notify('⚠ Stephan: …' mit new[:7]),
                          sha = old, zusätzlich 'gescheitert' = new
      'kaputt'          : schon der Rückweg wirft: notify('⚠ Stephan: …'), sha = old"""

def load_state(path: str) -> dict:
    """Fehlende Datei -> {}. Kaputtes JSON -> ValueError (abbrechen statt raten, Regel 7)."""

def save_state(path: str, state: dict) -> None:
    """Schreibt atomar (Temp-Datei + replace), Modus 600."""
```

`main()`: `git fetch` im Deploy-Klon (nur lesender Deploy-Key), CI-Stand von `main` per `gh api`, `decide`,
`run_deploy`, Zustand speichern (`last_good`, `failed`). `smoke`: `python3 -c "import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]" /root/*.py`
plus `python3 /root/worker_tick.py --help` bzw. der kleinste vorhandene Selbsttest der Takte (Worker wählt das passende
Argument aus den vorhandenen CLI-Optionen, begründet im PR). Läuft unter `flock`, Takt 5 Minuten.

## Nicht Teil dieser Aufgabe

- Kein Ausführen von `install.sh`, `apt`, `pip`, `systemctl restart` durch den Timer.
- Keine Änderung an Review, Familienregel, `auto_merge`, `acceptance-guard`, Merge-Bedingungen.
- Kein Autodeploy für andere Projekte (sc-digger hat seinen eigenen).
- Kein Schreibrecht des Deploy-Keys.

## Akzeptanztests

```python
"""Akzeptanztest: Entscheidungslogik des Autodeploy-Timers (ohne Netzwerk, gh und Server)."""

import importlib.util
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "sandbox" / "autodeploy_tick.py"
OLD, NEW = "a" * 40, "b" * 40


@pytest.fixture(scope="module")
def ad():
    spec = importlib.util.spec_from_file_location("autodeploy_tick", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("files, erwartet", [
    ([], "nichts"),
    (["sandbox/worker_tick.py", "README.md", "tests/test_x.py", "CHANGELOG.md", "docs/a.md", "LICENSE"], "kopieren"),
    ([".github/workflows/ci.yml"], "kopieren"),
    (["sandbox/install.sh"], "manuell"),
    (["sandbox/install-lib.sh"], "manuell"),
    (["sandbox/worker_tick.py", "sandbox/install.sh"], "manuell"),
    (["sandbox/systemd/qwen-webhook.service"], "manuell"),
    (["requirements.txt"], "manuell"),
    (["sandbox/neu.sh"], "manuell"),
    (["sandbox/unter/x.py"], "manuell"),
    (["unbekannt.bin"], "manuell"),
])
def test_classify_changes(ad, files, erwartet):
    assert ad.classify_changes(files) == erwartet


@pytest.mark.parametrize("local, remote, ci, failed, erwartet", [
    (OLD, OLD, "success", None, "nichts"),
    (OLD, OLD, "failure", None, "nichts"),
    (OLD, NEW, "success", None, "deployen"),
    (OLD, NEW, "pending", None, "warten"),
    (OLD, NEW, "unknown", None, "warten"),
    (OLD, NEW, "failure", None, "gesperrt"),
    (OLD, NEW, "success", NEW, "gesperrt"),
    (OLD, NEW, "success", "c" * 40, "deployen"),
])
def test_decide(ad, local, remote, ci, failed, erwartet):
    assert ad.decide(local, remote, ci, failed) == erwartet


def fakes(smoke=True, copy_fehler=0, rueckweg_fehler=False):
    calls, msgs = [], []
    zaehler = {"copy": 0}

    def checkout(sha):
        calls.append(("checkout", sha))
        if rueckweg_fehler and sha == OLD:
            raise OSError("checkout")

    def copy():
        calls.append(("copy",))
        zaehler["copy"] += 1
        if zaehler["copy"] <= copy_fehler:
            raise OSError("copy")

    def smoke_fn():
        calls.append(("smoke",))
        return smoke

    return calls, msgs, dict(checkout=checkout, copy=copy, smoke=smoke_fn, notify=msgs.append)


def test_deploy_ok(ad):
    calls, msgs, f = fakes()
    r = ad.run_deploy(OLD, NEW, changed=["sandbox/worker_tick.py"], **f)
    assert r["ergebnis"] == "ok" and r["sha"] == NEW
    assert calls == [("checkout", NEW), ("copy",), ("smoke",)]
    assert msgs == []


def test_deploy_rollback_bei_fehlgeschlagenem_smoke(ad):
    calls, msgs, f = fakes(smoke=False)
    r = ad.run_deploy(OLD, NEW, changed=["sandbox/worker_tick.py"], **f)
    assert r["ergebnis"] == "zurueckgerollt" and r["sha"] == OLD and r["gescheitert"] == NEW
    assert calls == [("checkout", NEW), ("copy",), ("smoke",), ("checkout", OLD), ("copy",)]
    assert len(msgs) == 1 and msgs[0].startswith("⚠ Stephan:") and NEW[:7] in msgs[0]


def test_deploy_rollback_wenn_copy_wirft(ad):
    calls, msgs, f = fakes(copy_fehler=1)
    r = ad.run_deploy(OLD, NEW, changed=["sandbox/worker_tick.py"], **f)
    assert r["ergebnis"] == "zurueckgerollt" and r["sha"] == OLD
    assert calls == [("checkout", NEW), ("copy",), ("checkout", OLD), ("copy",)]
    assert msgs and msgs[0].startswith("⚠ Stephan:")


def test_deploy_kaputt_wenn_der_rueckweg_scheitert(ad):
    calls, msgs, f = fakes(smoke=False, rueckweg_fehler=True)
    r = ad.run_deploy(OLD, NEW, changed=["sandbox/worker_tick.py"], **f)
    assert r["ergebnis"] == "kaputt" and r["sha"] == OLD
    assert msgs and all(m.startswith("⚠ Stephan:") for m in msgs)


def test_deploy_manuell_fasst_nichts_an(ad):
    calls, msgs, f = fakes()
    r = ad.run_deploy(OLD, NEW, changed=["sandbox/worker_tick.py", "sandbox/install.sh"], **f)
    assert r["ergebnis"] == "manuell" and r["sha"] == OLD
    assert calls == []
    assert len(msgs) == 1 and msgs[0].startswith("⚠ Stephan:") and "install.sh" in msgs[0]


def test_deploy_ohne_dateien_schiebt_nur_den_stand_vor(ad):
    calls, msgs, f = fakes()
    r = ad.run_deploy(OLD, NEW, changed=[], **f)
    assert r["ergebnis"] == "nichts" and r["sha"] == NEW and calls == [] and msgs == []


def test_state_roundtrip_atomar_und_privat(ad, tmp_path):
    p = tmp_path / "state.json"
    assert ad.load_state(str(p)) == {}
    ad.save_state(str(p), {"last_good": OLD, "failed": None})
    assert ad.load_state(str(p)) == {"last_good": OLD, "failed": None}
    assert (p.stat().st_mode & 0o777) == 0o600
    assert list(tmp_path.iterdir()) == [p]


def test_kaputter_state_bricht_ab(ad, tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{kaputt")
    with pytest.raises(ValueError):
        ad.load_state(str(p))
```

## Fertig, wenn

- [ ] Akzeptanztests grün
- [ ] `AUTODEPLOY` ist ohne Angabe 0; mit 1 richtet install.sh Service und Timer ein (Test mit Fake-systemctl wie in tests/test_install_lib.py)
- [ ] Der Timer läuft getrennt von Worker- und Review-Takt (eigene Unit, eigenes flock)
- [ ] AGENTS.md Regel 4 und Ablauf Schritt 7 nennen die Ausnahme genau; `tests/test_agents_md.py` bleibt grün
- [ ] README und CHANGELOG ergänzt

## Kontext

Wunsch: wie bei sc-digger nach dem Merge ohne SSH ausrollen. Hier bewusst enger gefasst (nur
Takt-Skripte, nur nach grünem CI, mit Rollback), weil ein Fehler in `worker_tick.py` oder
`review_tick.py` sonst Worker und Review lahmlegt. Der Timer ist unabhängig von beiden und kann
deshalb auch bei kaputtem Worker zurückrollen. Wartet auf Issue A (install.sh).
