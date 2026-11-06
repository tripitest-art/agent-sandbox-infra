import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_changelog_pr", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_changelog_pr"] = w
_spec.loader.exec_module(w)

PUSH_ENV = {"PUSH": "1"}


@pytest.fixture
def lage(monkeypatch, tmp_path):
    (tmp_path / "changelog.d").mkdir()
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(w, "push_env", lambda: PUSH_ENV)
    monkeypatch.setattr(w, "alarm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein Alarm")))
    aufrufe, exits = [], {}

    def fake_run(cmd, **kw):
        aufrufe.append((list(cmd), kw.get("env"), kw.get("cwd")))
        return subprocess.CompletedProcess(cmd, exits.get(cmd[1], 0), "AUSGABE-GEHEIM", "FEHLER-GEHEIM")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    return {"dir": tmp_path, "aufrufe": aufrufe, "exits": exits,
            "datei": tmp_path / "changelog.d" / "438.md"}


def test_stellt_um_committet_und_pusht(lage, capsys):
    lage["datei"].write_text("- benchmark_code: wartet (#438), siehe auch (#4380).\n", encoding="utf-8")
    assert w.changelog_pr_nummer(438, 439) is True
    assert lage["datei"].read_text(encoding="utf-8") == "- benchmark_code: wartet (#439), siehe auch (#4380).\n"
    assert [a[0] for a in lage["aufrufe"]] == [
        ["git", "add", "changelog.d/438.md"],
        ["git", "commit", "-m", "changelog: PR-Nummer #439 statt Issue-Nummer #438"],
        ["git", "push", "origin", "feature/issue-438"],
    ]
    assert [a[1] for a in lage["aufrufe"]] == [w.ENV, w.ENV, PUSH_ENV]
    assert all(a[2] == str(lage["dir"]) for a in lage["aufrufe"])
    assert "issue438: Changelog auf PR #439 umgestellt." in capsys.readouterr().out


@pytest.mark.parametrize("inhalt", [None, "- ohne Verweis\n", "- schon richtig (#439)\n", "- fast (#4380)\n"])
def test_nichts_zu_tun(lage, capsys, inhalt):
    if inhalt is not None:
        lage["datei"].write_text(inhalt, encoding="utf-8")
    assert w.changelog_pr_nummer(438, 439) is False
    assert lage["aufrufe"] == []
    assert capsys.readouterr().out == ""
    if inhalt is not None:
        assert lage["datei"].read_text(encoding="utf-8") == inhalt


def test_gleiche_nummer(lage):
    lage["datei"].write_text("- x (#438)\n", encoding="utf-8")
    assert w.changelog_pr_nummer(438, 438) is False
    assert lage["aufrufe"] == []


@pytest.mark.parametrize("schritt, anzahl", [("add", 1), ("commit", 2), ("push", 3)])
def test_fehlschlag_bricht_ab_und_loggt_ohne_ausgabe(lage, capsys, schritt, anzahl):
    lage["datei"].write_text("- x (#438)\n", encoding="utf-8")
    lage["exits"][schritt] = 1
    assert w.changelog_pr_nummer(438, 439) is False
    assert len(lage["aufrufe"]) == anzahl
    out = capsys.readouterr().out
    assert f"issue438: PR-Nummer im Changelog nicht nachgetragen (git {schritt}, Exit 1)." in out
    assert "GEHEIM" not in out


def test_wirft_nie(lage, monkeypatch, capsys):
    lage["datei"].write_text("- x (#438)\n", encoding="utf-8")

    def kaputt(cmd, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(w.subprocess, "run", kaputt)
    assert w.changelog_pr_nummer(438, 439) is False
    assert "issue438: PR-Nummer im Changelog nicht nachgetragen (FileNotFoundError)." in capsys.readouterr().out


# ---- Verdrahtung in open_pr() ----

def _open_pr_lage(monkeypatch, tmp_path, offen, url):
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", "## Vorlage\n")
    monkeypatch.setattr(w, "push_env", lambda: PUSH_ENV)
    reihenfolge, gerufen = [], []

    def fake_gh(*args):
        if args[:2] == ("pr", "list"):
            return offen
        if args[:2] == ("issue", "view"):
            return {"title": "Titel"}
        if args[:2] == ("pr", "create"):
            reihenfolge.append("pr create")
            return url
        raise AssertionError(args)

    def fake_run(cmd, **kw):
        if list(cmd[:3]) == ["git", "rev-list", "--count"]:
            return subprocess.CompletedProcess(cmd, 0, "3\n", "")
        if list(cmd[:2]) == ["git", "log"]:
            return subprocess.CompletedProcess(cmd, 0, "test\x00Umsetzung\x00", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_changelog(n, pr_no):
        reihenfolge.append("changelog")
        gerufen.append((n, pr_no))
        return False

    monkeypatch.setattr(w, "gh", fake_gh)
    monkeypatch.setattr(w.subprocess, "run", fake_run)
    monkeypatch.setattr(w, "changelog_pr_nummer", fake_changelog)
    return reihenfolge, gerufen


def test_open_pr_traegt_nach_dem_anlegen_nach(monkeypatch, tmp_path):
    reihenfolge, gerufen = _open_pr_lage(monkeypatch, tmp_path, [],
                                         "https://github.com/tripitest-art/agent-sandbox-infra/pull/439\n")
    assert w.open_pr(438) is True, "ein gescheitertes Nachtragen ändert den Rückgabewert nicht"
    assert gerufen == [(438, 439)]
    assert reihenfolge == ["pr create", "changelog"]


def test_open_pr_ohne_url_oder_mit_bestehendem_pr_nicht(monkeypatch, tmp_path):
    _, gerufen = _open_pr_lage(monkeypatch, tmp_path, [], "kein Link\n")
    assert w.open_pr(438) is True and gerufen == []
    _, gerufen = _open_pr_lage(monkeypatch, tmp_path, [{"number": 439}], "")
    assert w.open_pr(438) is True and gerufen == []
