"""Tests für `sandbox/gc.py` (Housekeeping: Worktrees, Branches, Belegungen) – Issue #225.

Ohne Netzwerk, ohne Server und ohne echtes Repository: Der Git-Aufruf kommt als `run` herein,
Belegungsdateien liegen in `tmp_path`. Damit prüfen die Tests die genaue Befehlsfolge, die
Filterung geschützter Branches und die Fehlerbehandlung (fehlender Klon, Git-Fehlercode).
"""

import os
import subprocess

from sandbox import gc


def _ok(cmd, stdout=""):
    return subprocess.CompletedProcess(cmd, 0, stdout, "")


def _fehler(cmd, rc=128):
    return subprocess.CompletedProcess(cmd, rc, "", "fatal")


# ---- prune_worktrees ----

def test_prune_worktrees_nutzt_das_repo_und_meldet_erfolg():
    aufrufe = []

    def fake_run(cmd):
        aufrufe.append(list(cmd))
        return _ok(cmd)

    assert gc.prune_worktrees("/tmp/repo", run=fake_run) is True
    assert aufrufe == [["git", "-C", "/tmp/repo", "worktree", "prune"]]


def test_prune_worktrees_falscher_ausgang():
    def fake_run(cmd):
        return _fehler(cmd, 1)

    assert gc.prune_worktrees("/tmp/repo", run=fake_run) is False


def test_prune_worktrees_faengt_fehlendes_git():
    def fake_run(cmd):
        raise FileNotFoundError("git fehlt")

    assert gc.prune_worktrees("/tmp/repo", run=fake_run) is False


def test_prune_worktrees_faengt_subprocess_fehler():
    def fake_run(cmd):
        raise subprocess.SubprocessError("kaputt")

    assert gc.prune_worktrees("/tmp/repo", run=fake_run) is False


# ---- cleanup_merged_branches ----

def test_cleanup_merged_branches_filtert_geschuetzte_und_aktive_branches():
    aufrufe = []
    geloescht = []

    def fake_run(cmd):
        aufrufe.append(list(cmd))
        if "--merged" in cmd:
            return _ok(cmd, "* main\n  feature/eins\n+ feature/zwei\n  master\n  develop\n")
        if "-d" in cmd:
            geloescht.append(cmd[-1])
            return _ok(cmd, "Deleted branch")
        return _ok(cmd)

    ergebnis = gc.cleanup_merged_branches("/tmp/repo", main_branch="develop", run=fake_run)
    assert ergebnis == ["feature/eins"]
    assert geloescht == ["feature/eins"]
    assert not any(b in geloescht for b in ("main", "master", "develop", "feature/zwei"))
    assert ["git", "-C", "/tmp/repo", "branch", "--merged", "develop"] == aufrufe[0]


def test_cleanup_merged_branches_meldet_nur_erfolgreich_geloeschte():
    def fake_run(cmd):
        if "--merged" in cmd:
            return _ok(cmd, "  feature/a\n  feature/b\n")
        if "-d" in cmd:
            if cmd[-1] == "feature/b":
                return _fehler(cmd, 1)      # z. B. nicht vollständig gemergt
            return _ok(cmd)
        return _ok(cmd)

    assert gc.cleanup_merged_branches("/tmp/repo", run=fake_run) == ["feature/a"]


def test_cleanup_merged_branches_ohne_merged_liste_loescht_nichts():
    def fake_run(cmd):
        return _fehler(cmd)                 # kein Repository / Git-Fehler

    assert gc.cleanup_merged_branches("/tmp/repo", run=fake_run) == []


def test_cleanup_merged_branches_faengt_fehler_beim_loeschen():
    def fake_run(cmd):
        if "--merged" in cmd:
            return _ok(cmd, "  feature/a\n  feature/b\n")
        raise subprocess.SubprocessError("kaputt")

    assert gc.cleanup_merged_branches("/tmp/repo", run=fake_run) == []


# ---- prune_claims ----

def _claim_datei(pfad, inhalt="x"):
    pfad.write_text(inhalt, encoding="utf-8")
    return pfad


def test_prune_claims_loescht_alte_done_und_claim_dateien(tmp_path):
    pfad = tmp_path / "claims"
    pfad.mkdir()
    alt_done = _claim_datei(pfad / "issue1.done")
    alt_claim = _claim_datei(pfad / "issue2.claim")
    jung = _claim_datei(pfad / "issue3.done")
    fremd = _claim_datei(pfad / "notiz.txt")
    for datei in (alt_done, alt_claim):
        os.utime(datei, (100.0, 100.0))
    os.utime(jung, (900.0, 900.0))

    assert gc.prune_claims(pfad, max_age_seconds=500.0, now=1000.0) == 2
    assert not alt_done.exists() and not alt_claim.exists()
    assert jung.exists() and fremd.exists()


def test_prune_claims_laesst_unterverzeichnisse_stehen(tmp_path):
    pfad = tmp_path / "claims"
    unter = pfad / "issue1.done"
    unter.mkdir(parents=True)
    (unter / "datei").write_text("x", encoding="utf-8")
    os.utime(unter, (100.0, 100.0))

    assert gc.prune_claims(pfad, max_age_seconds=500.0, now=1000.0) == 0
    assert unter.is_dir()


def test_prune_claims_fehlendes_verzeichnis_ist_kein_fehler(tmp_path):
    assert gc.prune_claims(tmp_path / "gibt-es-nicht", max_age_seconds=1.0) == 0


def test_prune_claims_genau_an_der_grenze_bleibt_stehen(tmp_path):
    pfad = tmp_path / "claims"
    pfad.mkdir()
    grenzfall = _claim_datei(pfad / "issue1.done")
    os.utime(grenzfall, (500.0, 500.0))

    assert gc.prune_claims(pfad, max_age_seconds=500.0, now=1000.0) == 0
    assert grenzfall.exists()


# ---- run_gc ----

def test_run_gc_aggregiert_und_ueberspringt_fehlende_klone(tmp_path):
    claims = tmp_path / "claims"
    claims.mkdir()
    alt = _claim_datei(claims / "issue1.done")
    os.utime(alt, (0.0, 0.0))

    vorhanden = tmp_path / "repo"
    vorhanden.mkdir()
    fehlt = tmp_path / "kein-repo"

    def fake_run(cmd):
        if "--merged" in cmd:
            return _ok(cmd, "  feature/alt\n* main\n")
        return _ok(cmd)

    stats = gc.run_gc(claims, [vorhanden, fehlt], run=fake_run)
    # prune_claims nutzt die echte Uhr; die Datei von 1970 ist in jedem Fall zu alt.
    assert stats == {"claims": 1, "branches": 1, "repos": 1}


def test_run_gc_git_fehler_bricht_den_durchlauf_nicht_ab(tmp_path):
    claims = tmp_path / "claims"
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    for pfad in (repo_a, repo_b):
        pfad.mkdir()

    def fake_run(cmd):
        if "--merged" in cmd and cmd[2].endswith("a"):
            return _fehler(cmd)
        if "worktree" in cmd and cmd[2].endswith("a"):
            return _fehler(cmd, 1)
        if "--merged" in cmd:
            return _ok(cmd, "  feature/b\n")
        return _ok(cmd)

    stats = gc.run_gc(claims, [repo_a, repo_b], run=fake_run)
    assert stats == {"claims": 0, "branches": 1, "repos": 2}


def test_run_gc_faengt_fehlendes_git_bei_jedem_repo(tmp_path):
    claims = tmp_path / "claims"
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(cmd):
        raise FileNotFoundError("git fehlt")

    assert gc.run_gc(claims, [repo], run=fake_run) == {"claims": 0, "branches": 0, "repos": 1}
