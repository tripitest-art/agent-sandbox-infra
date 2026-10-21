"""Tests für scripts/ws.py (Worktrees der Planer-Umgebung; lokales origin, ohne Netzwerk)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Modul per Pfad laden (scripts/ ist kein Paket)
_spec = importlib.util.spec_from_file_location("ws", ROOT / "scripts" / "ws.py")
ws = importlib.util.module_from_spec(_spec)
sys.modules["ws"] = ws
_spec.loader.exec_module(ws)


def git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def commit_file(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    git("add", name, cwd=repo)
    git("commit", "-m", f"{name} anlegen", cwd=repo)


@pytest.fixture
def lage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Ein lokales origin, ein Seed-Klon zum Nachschieben von Commits und der Hauptklon `demo`."""
    # Weder die globale Git-Konfiguration noch deren Hooks des Rechners dürfen mitspielen.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for rolle in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{rolle}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{rolle}_EMAIL", "test@example.test")

    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    projects = tmp_path / "projects"
    work = tmp_path / "work"
    projects.mkdir()
    git("init", "--bare", "-b", "main", str(origin))
    git("init", "-b", "main", str(seed))
    commit_file(seed, "README.txt", "erster Stand\n")
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    git("clone", str(origin), str(projects / "demo"))
    return {"origin": origin, "seed": seed, "projects": projects, "work": work}


def test_legt_worktree_auf_neuem_branch_von_origin_main_an(lage):
    ziel = ws.create("claude", "demo", "aufgabe-1", lage["projects"], lage["work"])

    assert ziel == lage["work"] / "claude" / "demo-aufgabe-1"
    assert (ziel / "README.txt").read_text(encoding="utf-8") == "erster Stand\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=ziel) == "feature/aufgabe-1"
    assert git("rev-parse", "HEAD", cwd=ziel) == git("rev-parse", "main", cwd=lage["origin"])


def test_geht_vom_aktuellen_origin_main_aus_nicht_vom_alten_klon_stand(lage):
    """Der Hauptklon wird nie gepullt; ohne vorheriges Fetch läge der Branch auf dem alten Stand."""
    commit_file(lage["seed"], "neu.txt", "zweiter Stand\n")
    git("push", "origin", "main", cwd=lage["seed"])

    ziel = ws.create("agy", "demo", "nachschub", lage["projects"], lage["work"])

    assert (ziel / "neu.txt").exists()
    assert git("rev-parse", "HEAD", cwd=ziel) == git("rev-parse", "main", cwd=lage["origin"])


def test_neuer_branch_hat_keinen_upstream(lage):
    """Ohne --no-track folgte feature/<kurz> dem origin/main und ein `git push` wirkte dorthin."""
    ws.create("claude", "demo", "aufgabe-1", lage["projects"], lage["work"])

    upstream = git("for-each-ref", "--format=%(upstream)", "refs/heads/feature/aufgabe-1", cwd=lage["projects"] / "demo")

    assert upstream == ""


def test_jedes_werkzeug_bekommt_seinen_eigenen_ordner(lage):
    a = ws.create("claude", "demo", "eins", lage["projects"], lage["work"])
    b = ws.create("opencode", "demo", "zwei", lage["projects"], lage["work"])

    assert a.parent.name == "claude" and b.parent.name == "opencode"
    assert a != b


def test_bestehender_zielordner_wird_nicht_ueberschrieben(lage):
    ziel = ws.create("claude", "demo", "aufgabe-1", lage["projects"], lage["work"])
    (ziel / "eigene.txt").write_text("meine Arbeit\n", encoding="utf-8")

    with pytest.raises(ws.WsError, match="Existiert schon"):
        ws.create("claude", "demo", "aufgabe-1", lage["projects"], lage["work"])

    assert (ziel / "eigene.txt").read_text(encoding="utf-8") == "meine Arbeit\n"


def test_gleicher_branch_fuer_ein_anderes_werkzeug_wird_gemeldet(lage):
    ws.create("claude", "demo", "aufgabe-1", lage["projects"], lage["work"])

    with pytest.raises(ws.WsError, match=r"feature/aufgabe-1"):
        ws.create("opencode", "demo", "aufgabe-1", lage["projects"], lage["work"])


def test_fehlender_klon_wird_gemeldet(lage):
    with pytest.raises(ws.WsError, match="Kein Klon"):
        ws.create("claude", "gibtsnicht", "aufgabe-1", lage["projects"], lage["work"])


def test_nicht_erreichbares_origin_wird_gemeldet_und_legt_nichts_an(lage):
    lage["origin"].rename(lage["origin"].with_name("weg.git"))

    with pytest.raises(ws.WsError, match="fetch"):
        ws.create("claude", "demo", "aufgabe-1", lage["projects"], lage["work"])

    assert not (lage["work"] / "claude" / "demo-aufgabe-1").exists()


def test_unbekanntes_werkzeug_wird_abgelehnt():
    with pytest.raises(ws.WsError, match="Unbekanntes Werkzeug"):
        ws.validate("cursor", "demo", "aufgabe-1")


@pytest.mark.parametrize("kurz", ["", "Foo", "a/b", "-x", "a b", "..", "x_y", "a\\b"])
def test_unbrauchbarer_kurzname_wird_abgelehnt(kurz):
    with pytest.raises(ws.WsError, match="Kurzname"):
        ws.validate("claude", "demo", kurz)


@pytest.mark.parametrize("repo", ["", "../x", "a/b", ".hidden", "a..b", "a\\b"])
def test_repo_name_mit_pfadanteil_wird_abgelehnt(repo):
    with pytest.raises(ws.WsError, match="Repo-Name"):
        ws.validate("claude", repo, "aufgabe-1")


def test_main_meldet_ziel_und_entwurfsordner(lage, capsys):
    env = {"WS_PROJECTS": str(lage["projects"]), "WS_WORK": str(lage["work"])}

    code = ws.main(["claude", "demo", "aufgabe-1"], env=env)

    out = capsys.readouterr().out
    assert code == 0
    assert "Worktree:" in out and "feature/aufgabe-1" in out
    assert str(lage["work"] / "entwuerfe") in out


def test_main_gibt_fehler_mit_exitcode_1_aus(lage, capsys):
    env = {"WS_PROJECTS": str(lage["projects"]), "WS_WORK": str(lage["work"])}

    code = ws.main(["claude", "demo", "Gross"], env=env)

    assert code == 1
    assert capsys.readouterr().err.startswith("Fehler:")


def test_main_lehnt_unbekanntes_werkzeug_per_argparse_ab(capsys):
    with pytest.raises(SystemExit) as exc:
        ws.main(["cursor", "demo", "aufgabe-1"], env={})

    assert exc.value.code == 2
