"""Tests für die pre-receive-Sperre (`sandbox/hooks/`): kein Push in ein Haupt-Repo.

Ohne Netzwerk: Quelle, Haupt-Repo und Klon liegen in `tmp_path`, Git läuft lokal. Der
erste Push auf einen freien Branch geht durch — damit belegt der Test, dass danach der
Hook blockiert und nicht `receive.denyCurrentBranch`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "sandbox" / "hooks" / "pre-receive-sperre"
INSTALLER = ROOT / "sandbox" / "hooks" / "install-sperre.sh"
MELDUNG = "Push in Haupt-Repo auf CT 106 gesperrt"


@pytest.fixture(autouse=True)
def git_umgebung(monkeypatch):
    """Weder globale Git-Konfiguration noch Hooks des Rechners dürfen mitspielen."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for rolle in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{rolle}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{rolle}_EMAIL", "test@example.test")


def git(*args: str, cwd: Path | None = None, check: bool = True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def repo_mit_commit(pfad: Path) -> Path:
    pfad.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=pfad)
    (pfad / "datei.txt").write_text("eins\n", encoding="utf-8")
    git("add", "datei.txt", cwd=pfad)
    git("commit", "-q", "-m", "erster", cwd=pfad)
    return pfad


def sperre_installieren(projects: Path):
    return subprocess.run(["bash", str(INSTALLER), str(projects)],
                          check=False, capture_output=True, text=True)


def refs(repo: Path) -> str:
    return git("for-each-ref", "--format=%(refname) %(objectname)", cwd=repo).stdout


def test_push_und_ref_loeschung_werden_abgewiesen(tmp_path):
    projects = tmp_path / "projects"
    haupt = projects / "haupt"
    haupt.parent.mkdir()
    git("clone", "-q", str(repo_mit_commit(tmp_path / "quelle")), str(haupt))
    klon = tmp_path / "klon"
    git("clone", "-q", str(haupt), str(klon))
    (klon / "neu.txt").write_text("zwei\n", encoding="utf-8")
    git("add", "neu.txt", cwd=klon)
    git("commit", "-q", "-m", "zweiter", cwd=klon)

    # Ohne Hook geht derselbe Push (neuer Branch, kein denyCurrentBranch im Weg).
    assert git("push", "-q", "origin", "HEAD:refs/heads/zz-frei", cwd=klon,
               check=False).returncode == 0
    assert sperre_installieren(projects).returncode == 0
    vorher = refs(haupt)

    neu = git("push", "origin", "HEAD:refs/heads/zz-sperrtest", cwd=klon, check=False)
    weg = git("push", "origin", ":refs/heads/zz-frei", cwd=klon, check=False)
    git("tag", "v1", cwd=klon)                 # ohne Tag wäre --tags ein No-op ohne Hook-Aufruf
    tags = git("push", "origin", "--tags", cwd=klon, check=False)
    pfad = git("push", str(haupt), "HEAD:refs/heads/zz-pfad", cwd=klon, check=False)

    for ergebnis in (neu, weg, tags, pfad):
        assert ergebnis.returncode != 0
        assert MELDUNG in ergebnis.stderr
    assert refs(haupt) == vorher


def test_installer_laesst_fremden_hook_stehen_und_ist_wiederholbar(tmp_path):
    projects = tmp_path / "projects"
    eigenes = repo_mit_commit(projects / "eigenes")
    fremdes = repo_mit_commit(projects / "fremdes")
    fremd_hook = fremdes / ".git" / "hooks" / "pre-receive"
    fremd_hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (projects / "kein-repo").mkdir()

    erst = sperre_installieren(projects)

    assert erst.returncode == 0
    ziel = eigenes / ".git" / "hooks" / "pre-receive"
    assert ziel.read_bytes() == HOOK.read_bytes()
    assert os.access(ziel, os.X_OK)
    assert fremd_hook.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
    assert "FREMDER HOOK" in erst.stderr

    zweit = sperre_installieren(projects)

    assert zweit.returncode == 0
    assert "Sperre aktiv" in zweit.stdout


def test_installer_ohne_repos_meldet_das(tmp_path):
    ergebnis = sperre_installieren(tmp_path)

    assert ergebnis.returncode == 0
    assert "Keine Git-Repos" in ergebnis.stdout
