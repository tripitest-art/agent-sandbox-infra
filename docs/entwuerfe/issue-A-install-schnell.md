# Entwurf A: install.sh überspringt Unverändertes und meldet, was es tut

Titel: `install.sh: Paketinstallation und pip nur bei Änderung, Fortschrittsmeldungen`
Labels: `worker-task`, `entwurf`, `phase-?`, `agent-?`
Merge-Modus: manuell durch Stephan (Betrieb, Server)

## Ziel

Ein erneuter Lauf von `install.sh` nach `git pull` dauert nur noch Sekunden, wenn sich weder die
Paketliste noch eine `requirements.txt` geändert hat. Die langen Schritte melden, was sie tun
(heute ist `pip install -q` stumm und der Lauf wirkt hängend).

## Betroffene Dateien

- sandbox/install-lib.sh (neue Funktionen)
- sandbox/install.sh (Aufruf der Funktionen, Meldungen)
- tests/test_install_lib.py
- README.md, CHANGELOG.md

## Schnittstellen

```bash
# sandbox/install-lib.sh
STAMP_DIR=${AGENT_SANDBOX_STAMPS:-/var/lib/agent-sandbox}   # Marken, ein Verzeichnis, Modus 700

deps_changed() {   # $1 = Name (nur [A-Za-z0-9._-]), $2 = Klon-Ordner
  # Rückgabe 0: pip muss laufen. 1: nicht nötig. 2: Name ungültig (Meldung auf stderr).
  # 0 wenn: INSTALL_VOLL=1, oder ${VENV_DIR:-/root/venv}/bin/python fehlt, oder es gibt keine
  #         Marke $STAMP_DIR/<Name>.requirements, oder deren Inhalt ≠ sha256 von $2/requirements.txt.
  # 1 wenn $2/requirements.txt fehlt (nichts zu installieren) oder die Marke passt.
}

deps_mark() {      # $1, $2 wie oben: schreibt die Marke (sha256 der requirements.txt); kein Fehler, wenn die Datei fehlt
}

apt_needed() {     # Rückgabe 0: apt-get muss laufen. 1: nicht nötig.
  # 0 wenn: INSTALL_VOLL=1, oder eines der Programme aus ${APT_PROGRAMS:-git gh tmux curl ffmpeg}
  #         nicht per `command -v` gefunden wird, oder ${APT_KEYRING:-/etc/apt/keyrings/githubcli-archive-keyring.gpg} fehlt.
}
```

`install.sh`: `apt-get update/install` und das Einrichten des gh-Paketarchivs nur `if apt_needed`.
In der Schleife über `project_workdirs`: `pip install` nur `if deps_changed "$name" "$dir"`, danach
`deps_mark`. Für `pytest` bleibt es bei einem Aufruf, aber ebenfalls nur, wenn `pytest` im venv fehlt.
Vor jedem übersprungenen oder langen Schritt eine Zeile, z. B. `Pakete: unverändert, übersprungen`
bzw. `Abhängigkeiten für sc-digger werden installiert (kann einige Minuten dauern) …`.
`INSTALL_VOLL=1 bash install.sh` erzwingt alles.

## Nicht Teil dieser Aufgabe

- Kein neuer Schalter `--schnell`, keine Änderung an OpenCode-/Qwen-Installation, Timern, Units, Geheimnissen.
- Keine Änderung an `worker_tick.py`, `review_tick.py`, `.github/`.
- Kein Autodeploy (eigenes Issue B).

## Akzeptanztests

```python
"""Akzeptanztest: install-lib.sh überspringt unveränderte Installationsschritte."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

if not shutil.which("bash"):
    if os.environ.get("CI"):
        raise RuntimeError("bash fehlt, in CI Pflicht")
    pytest.skip("bash fehlt auf diesem Rechner", allow_module_level=True)

SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def sh(script, tmp_path, **env):
    full = {**os.environ, "SCRIPT_DIR": str(SANDBOX), "INFRA_DIR": str(tmp_path),
            "AGENT_SANDBOX_CONF": str(tmp_path / "conf"), "AGENT_SANDBOX_STAMPS": str(tmp_path / "stamps"),
            "VENV_DIR": str(tmp_path / "venv"), **env}
    return subprocess.run(["bash", "-c", f". {SANDBOX}/install-lib.sh\n{script}"],
                          capture_output=True, text=True, env=full)


@pytest.fixture
def klon(tmp_path):
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")
    d = tmp_path / "klon"
    d.mkdir()
    (d / "requirements.txt").write_text("requests==2.0\n")
    return d


def frage(tmp_path, klon, vorher="", **env):
    r = sh(f'{vorher}\nif deps_changed sc "{klon}"; then echo ja; else echo nein; fi', tmp_path, **env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_ohne_marke_muss_pip_laufen(tmp_path, klon):
    assert frage(tmp_path, klon) == "ja"


def test_mit_passender_marke_entfaellt_pip(tmp_path, klon):
    assert frage(tmp_path, klon, f'deps_mark sc "{klon}"') == "nein"


def test_geaenderte_requirements_loesen_pip_aus(tmp_path, klon):
    sh(f'deps_mark sc "{klon}"', tmp_path)
    (klon / "requirements.txt").write_text("requests==2.1\n")
    assert frage(tmp_path, klon) == "ja"


def test_ohne_requirements_ist_nichts_zu_tun(tmp_path, klon):
    (klon / "requirements.txt").unlink()
    assert frage(tmp_path, klon) == "nein"


def test_fehlendes_venv_loest_pip_aus_trotz_marke(tmp_path, klon):
    sh(f'deps_mark sc "{klon}"', tmp_path)
    (tmp_path / "venv" / "bin" / "python").unlink()
    assert frage(tmp_path, klon) == "ja"


def test_install_voll_erzwingt_pip(tmp_path, klon):
    assert frage(tmp_path, klon, f'deps_mark sc "{klon}"', INSTALL_VOLL="1") == "ja"


def test_ungueltiger_name_ist_ein_fehler_ohne_marke(tmp_path, klon):
    r = sh(f'deps_changed "../x" "{klon}"; echo rc=$?', tmp_path)
    assert "rc=2" in r.stdout and r.stderr.strip()
    assert not (tmp_path / "stamps").exists() or not any((tmp_path / "stamps").iterdir())


def apt(tmp_path, **env):
    r = sh('if apt_needed; then echo ja; else echo nein; fi', tmp_path, **env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_apt_entfaellt_wenn_alles_da_ist(tmp_path):
    keyring = tmp_path / "k.gpg"
    keyring.write_text("x")
    assert apt(tmp_path, APT_PROGRAMS="bash sh", APT_KEYRING=str(keyring)) == "nein"


def test_apt_laeuft_bei_fehlendem_programm(tmp_path):
    keyring = tmp_path / "k.gpg"
    keyring.write_text("x")
    assert apt(tmp_path, APT_PROGRAMS="bash gibt-es-nicht-xyz", APT_KEYRING=str(keyring)) == "ja"


def test_apt_laeuft_bei_fehlendem_keyring(tmp_path):
    assert apt(tmp_path, APT_PROGRAMS="bash", APT_KEYRING=str(tmp_path / "fehlt.gpg")) == "ja"


def test_apt_laeuft_bei_install_voll(tmp_path):
    keyring = tmp_path / "k.gpg"
    keyring.write_text("x")
    assert apt(tmp_path, APT_PROGRAMS="bash", APT_KEYRING=str(keyring), INSTALL_VOLL="1") == "ja"


def test_install_sh_nutzt_die_pruefungen_und_ist_syntaktisch_ok():
    text = (SANDBOX / "install.sh").read_text()
    assert "apt_needed" in text and "deps_changed" in text and "deps_mark" in text
    assert subprocess.run(["bash", "-n", str(SANDBOX / "install.sh")]).returncode == 0
```

## Fertig, wenn

- [ ] Akzeptanztests grün
- [ ] `bash -n sandbox/install.sh` fehlerfrei
- [ ] Ein Lauf ohne Änderungen überspringt apt und pip und sagt das im Log
- [ ] README (Abschnitt Einrichtung: `INSTALL_VOLL=1`) und CHANGELOG ergänzt

## Kontext

Nach `git pull` hing der Lauf scheinbar nach „Klon steht auf origin/main“: `pip install -q` für jeden
Klon (install.sh, Schleife über `project_workdirs`) ist stumm. Regel 8: install.sh ist ein Engpass,
Issue B (Autodeploy) wartet auf dieses Issue.
