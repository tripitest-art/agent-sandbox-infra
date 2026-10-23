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


# --- ws plan: fester Planungs-Worktree auf origin/main -------------------------------------------


def _ist_detached(ziel: Path) -> bool:
    return subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=ziel, capture_output=True).returncode != 0


def test_plan_legt_worktree_ohne_branch_auf_origin_main_an(lage):
    ziel, zustand = ws.plan("opencode", "demo", lage["projects"], lage["work"])

    assert ziel == lage["work"] / "opencode" / "demo-planung"
    assert zustand == "angelegt"
    assert (ziel / "README.txt").read_text(encoding="utf-8") == "erster Stand\n"
    assert _ist_detached(ziel)
    assert git("rev-parse", "HEAD", cwd=ziel) == git("rev-parse", "main", cwd=lage["origin"])
    # Es entsteht kein Branch, den man versehentlich pushen könnte.
    assert git("branch", "--list", "feature/*", cwd=lage["projects"] / "demo") == ""


def test_plan_zieht_den_bestehenden_worktree_auf_den_neuen_origin_stand(lage):
    ziel, _ = ws.plan("claude", "demo", lage["projects"], lage["work"])
    commit_file(lage["seed"], "neu.txt", "zweiter Stand\n")
    git("push", "origin", "main", cwd=lage["seed"])

    ziel2, zustand = ws.plan("claude", "demo", lage["projects"], lage["work"])

    assert ziel2 == ziel and zustand == "aktualisiert"
    assert (ziel / "neu.txt").read_text(encoding="utf-8") == "zweiter Stand\n"
    assert git("rev-parse", "HEAD", cwd=ziel) == git("rev-parse", "main", cwd=lage["origin"])
    assert _ist_detached(ziel)


def test_plan_ohne_neuen_stand_bleibt_unveraendert_und_fehlerfrei(lage):
    ziel, _ = ws.plan("claude", "demo", lage["projects"], lage["work"])
    vorher = git("rev-parse", "HEAD", cwd=ziel)

    _, zustand = ws.plan("claude", "demo", lage["projects"], lage["work"])

    assert zustand == "aktualisiert"
    assert git("rev-parse", "HEAD", cwd=ziel) == vorher


def test_plan_ueberschreibt_keine_lokalen_aenderungen(lage):
    ziel, _ = ws.plan("claude", "demo", lage["projects"], lage["work"])
    (ziel / "README.txt").write_text("meine Notiz\n", encoding="utf-8")
    commit_file(lage["seed"], "neu.txt", "zweiter Stand\n")
    git("push", "origin", "main", cwd=lage["seed"])

    with pytest.raises(ws.WsError, match="lokale Änderungen"):
        ws.plan("claude", "demo", lage["projects"], lage["work"])

    assert (ziel / "README.txt").read_text(encoding="utf-8") == "meine Notiz\n"
    assert not (ziel / "neu.txt").exists()


def test_plan_ueberschreibt_keine_neuen_ungetrackten_dateien(lage):
    ziel, _ = ws.plan("claude", "demo", lage["projects"], lage["work"])
    (ziel / "entwurf.md").write_text("Idee\n", encoding="utf-8")

    with pytest.raises(ws.WsError, match="lokale Änderungen"):
        ws.plan("claude", "demo", lage["projects"], lage["work"])

    assert (ziel / "entwurf.md").exists()


def test_plan_lehnt_einen_worktree_auf_einem_branch_ab(lage):
    ziel, _ = ws.plan("claude", "demo", lage["projects"], lage["work"])
    git("checkout", "-b", "feature/versehentlich", cwd=ziel)

    with pytest.raises(ws.WsError, match="Branch"):
        ws.plan("claude", "demo", lage["projects"], lage["work"])

    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=ziel) == "feature/versehentlich"


def test_plan_fasst_einen_fremden_ordner_gleichen_namens_nicht_an(lage):
    fremd = lage["work"] / "claude" / "demo-planung"
    fremd.mkdir(parents=True)
    (fremd / "wichtig.txt").write_text("nicht anfassen\n", encoding="utf-8")

    with pytest.raises(ws.WsError, match="kein Worktree"):
        ws.plan("claude", "demo", lage["projects"], lage["work"])

    assert (fremd / "wichtig.txt").read_text(encoding="utf-8") == "nicht anfassen\n"


def test_plan_meldet_fehlenden_klon_und_nicht_erreichbares_origin(lage):
    with pytest.raises(ws.WsError, match="Kein Klon"):
        ws.plan("claude", "gibtsnicht", lage["projects"], lage["work"])

    lage["origin"].rename(lage["origin"].with_name("weg.git"))
    with pytest.raises(ws.WsError, match="fetch"):
        ws.plan("claude", "demo", lage["projects"], lage["work"])
    assert not (lage["work"] / "claude" / "demo-planung").exists()


def test_plan_lehnt_unbekanntes_werkzeug_und_repo_mit_pfadanteil_ab(lage):
    with pytest.raises(ws.WsError, match="Unbekanntes Werkzeug"):
        ws.plan("cursor", "demo", lage["projects"], lage["work"])
    with pytest.raises(ws.WsError, match="Repo-Name"):
        ws.plan("claude", "../demo", lage["projects"], lage["work"])


def test_plan_jedes_werkzeug_hat_seinen_eigenen_planungs_worktree(lage):
    a, _ = ws.plan("claude", "demo", lage["projects"], lage["work"])
    b, _ = ws.plan("opencode", "demo", lage["projects"], lage["work"])

    assert a != b and a.parent.name == "claude" and b.parent.name == "opencode"


def test_main_plan_meldet_zustand_und_hinweis(lage, capsys):
    env = {"WS_PROJECTS": str(lage["projects"]), "WS_WORK": str(lage["work"])}

    code = ws.main(["plan", "opencode", "demo"], env=env)

    out = capsys.readouterr().out
    assert code == 0
    assert "Planungs-Worktree angelegt" in out and "demo-planung" in out
    assert "ws <werkzeug> <repo> <kurz>" in out
    assert str(lage["work"] / "entwuerfe") in out


def test_main_plan_gibt_fehler_mit_exitcode_1_aus(lage, capsys):
    env = {"WS_PROJECTS": str(lage["projects"]), "WS_WORK": str(lage["work"])}

    code = ws.main(["plan", "claude", "gibtsnicht"], env=env)

    assert code == 1
    assert capsys.readouterr().err.startswith("Fehler:")


def test_main_plan_lehnt_unbekanntes_werkzeug_per_argparse_ab():
    with pytest.raises(SystemExit) as exc:
        ws.main(["plan", "cursor", "demo"], env={})

    assert exc.value.code == 2


def test_aufruf_ohne_plan_funktioniert_unveraendert(lage, capsys):
    env = {"WS_PROJECTS": str(lage["projects"]), "WS_WORK": str(lage["work"])}

    assert ws.main(["claude", "demo", "aufgabe-1"], env=env) == 0
    assert "feature/aufgabe-1" in capsys.readouterr().out
