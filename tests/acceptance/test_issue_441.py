import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ws_aufraeumen", ROOT / "scripts" / "ws.py")
ws = importlib.util.module_from_spec(_spec)
sys.modules["ws_aufraeumen"] = ws
_spec.loader.exec_module(ws)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, name, text, msg=None):
    (repo / name).write_text(text, encoding="utf-8")
    git("add", name, cwd=repo)
    git("commit", "-m", msg or name, cwd=repo)


@pytest.fixture
def lage(tmp_path, monkeypatch):
    """Lokales origin, Seed-Klon (spielt GitHub: Squash-Merge auf main) und Hauptklon demo mit fünf Worktrees."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for rolle in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{rolle}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{rolle}_EMAIL", "test@example.test")
    origin, seed = tmp_path / "origin.git", tmp_path / "seed"
    projects, work = tmp_path / "projects", tmp_path / "work"
    projects.mkdir()
    git("init", "--bare", "-b", "main", str(origin))
    git("init", "-b", "main", str(seed))
    commit(seed, "README.txt", "erster Stand\n")
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    git("clone", str(origin), str(projects / "demo"))

    wt = {k: ws.create("claude", "demo", k, projects, work) for k in ("fertig", "offen", "dreckig", "leer")}
    for k, inhalt in (("fertig", "a\n"), ("offen", "b\n"), ("dreckig", "c\n")):
        commit(wt[k], f"{k}.txt", inhalt)
        git("push", "-u", "origin", f"feature/{k}", cwd=wt[k])
    # „GitHub“ squash-mergt fertig und dreckig: gleicher Inhalt auf main, anderer Commit.
    git("pull", "-q", "origin", "main", cwd=seed)
    commit(seed, "fertig.txt", "a\n", "Fertig (#1)")
    commit(seed, "dreckig.txt", "c\n", "Dreckig (#2)")
    git("push", "origin", "main", cwd=seed)
    (wt["dreckig"] / "notiz.md").write_text("ungesichert\n", encoding="utf-8")
    plan, _ = ws.plan("claude", "demo", projects, work)
    return {"projects": projects, "work": work, "wt": wt, "plan": plan, "demo": projects / "demo"}


def test_kandidaten_und_gruende(lage):
    k = {p: (b, ok, g) for p, b, ok, g in ws.aufraeum_kandidaten(lage["projects"], lage["work"])}
    wt = lage["wt"]
    assert k[wt["fertig"]] == ("feature/fertig", True, "in origin/main")
    assert k[wt["offen"]] == ("feature/offen", False, "nicht in origin/main")
    assert k[wt["dreckig"]] == ("feature/dreckig", False, "ungesicherte Änderungen")
    assert k[wt["leer"]] == ("feature/leer", False, "noch nichts committet")
    assert k[lage["plan"]] == ("", False, "ohne Branch")
    assert lage["demo"] not in k, "der Hauptklon selbst ist kein Kandidat"
    assert [p for p, *_ in ws.aufraeum_kandidaten(lage["projects"], lage["work"])] == sorted(k)


def test_ohne_ja_bleibt_alles(lage, monkeypatch, capsys):
    monkeypatch.setenv("WS_PROJECTS", str(lage["projects"]))
    monkeypatch.setenv("WS_WORK", str(lage["work"]))
    assert ws.main(["aufraeumen"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert f"würde entfernen: {lage['wt']['fertig']} (feature/fertig, in origin/main)" in out
    assert f"bleibt: {lage['wt']['offen']} (feature/offen, nicht in origin/main)" in out
    assert f"bleibt: {lage['plan']} (-, ohne Branch)" in out
    assert out[-1] == "Nichts geändert. Mit --ja entfernen."
    assert all(p.exists() for p in lage["wt"].values())
    assert "feature/fertig" in git("branch", "--list", "feature/fertig", cwd=lage["demo"])


def test_mit_ja_nur_das_erledigte(lage, monkeypatch, capsys):
    monkeypatch.setenv("WS_PROJECTS", str(lage["projects"]))
    monkeypatch.setenv("WS_WORK", str(lage["work"]))
    assert ws.main(["aufraeumen", "--ja"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert f"entfernt: {lage['wt']['fertig']} (feature/fertig, in origin/main)" in out
    assert not lage["wt"]["fertig"].exists()
    assert git("branch", "--list", "feature/fertig", cwd=lage["demo"]) == ""
    for k in ("offen", "dreckig", "leer"):
        assert lage["wt"][k].exists(), k
    assert (lage["wt"]["dreckig"] / "notiz.md").read_text(encoding="utf-8") == "ungesichert\n"
    assert lage["plan"].exists()
    assert "feature/offen" in git("ls-remote", "--heads", "origin", cwd=lage["demo"]), "nichts auf origin gelöscht"
    assert "feature/fertig" in git("ls-remote", "--heads", "origin", cwd=lage["demo"]), "nichts auf origin gelöscht"


def test_aufraeumen_funktion(lage):
    assert ws.aufraeumen(lage["projects"], lage["work"]) == []
    assert lage["wt"]["fertig"].exists()
    assert ws.aufraeumen(lage["projects"], lage["work"], ja=True) == [lage["wt"]["fertig"]]


def test_unerreichbares_origin_ueberspringt_den_klon(lage):
    git("remote", "set-url", "origin", str(lage["projects"] / "gibt-es-nicht.git"), cwd=lage["demo"])
    assert ws.aufraeum_kandidaten(lage["projects"], lage["work"]) == []


def test_bestehende_befehle_unveraendert(lage, monkeypatch, capsys):
    monkeypatch.setenv("WS_PROJECTS", str(lage["projects"]))
    monkeypatch.setenv("WS_WORK", str(lage["work"]))
    assert ws.main(["claude", "demo", "neu"]) == 0
    assert (lage["work"] / "claude" / "demo-neu").is_dir()
