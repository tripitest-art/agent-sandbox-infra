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


# ---- cleanup_squashed_branches ----

def test_cleanup_squashed_branches_loescht_nur_vollstaendig_enthaltene():
    geloescht = []

    def fake_run(cmd):
        if cmd[3] == "fetch":
            return _ok(cmd)
        if cmd[3] == "for-each-ref":
            return _ok(cmd, "main\nfeature/fertig\nfeature/offen\nfeature/leer\n")
        if cmd[3] == "worktree":
            return _ok(cmd, "worktree /x\nHEAD abc\nbranch refs/heads/feature/im-worktree\n\n")
        if cmd[3] == "merge-base":
            return _ok(cmd, "basis\n")
        if cmd[3] == "diff" and "--name-only" in cmd:
            return _ok(cmd, "" if cmd[-1] == "feature/leer" else "a.txt\n")
        if cmd[3] == "diff" and "--quiet" in cmd:
            return _ok(cmd) if "feature/fertig" in cmd else _fehler(cmd, 1)
        if cmd[3] == "branch":
            geloescht.append(cmd[-1])
            return _ok(cmd)
        return _ok(cmd)

    assert gc.cleanup_squashed_branches("/tmp/repo", run=fake_run) == ["feature/fertig"]
    assert geloescht == ["feature/fertig"]


def test_cleanup_squashed_branches_fetch_fehler_loescht_nichts():
    def fake_run(cmd):
        return _fehler(cmd, 1)

    assert gc.cleanup_squashed_branches("/tmp/repo", run=fake_run) == []


def test_cleanup_squashed_branches_faengt_fehler():
    def fake_run(cmd):
        raise subprocess.SubprocessError("kaputt")

    assert gc.cleanup_squashed_branches("/tmp/repo", run=fake_run) == []


# ---- streukopien ----

def test_streukopien_ignoriert_bekannte_pfade_und_unterpfade(tmp_path):
    now = 1000.0
    ort = tmp_path / "ort"
    ort.mkdir()
    (ort / "sc-digger" / ".git").mkdir(parents=True)
    os.utime(ort / "sc-digger", (100.0, 100.0))

    assert gc.streukopien(orte=[str(ort)], bekannt=[str(ort)], now=now) == []


def test_streukopien_fehlender_ort_ist_kein_fehler(tmp_path):
    assert gc.streukopien(orte=[str(tmp_path / "fehlt")], now=1000.0) == []


# ---- neue_streukopien ----

def test_neue_streukopien_schreibfehler_wirft_nicht(tmp_path):
    marke = str(tmp_path / "fehlt" / "marke.json")

    assert gc.neue_streukopien(["/tmp/a"], marke) == ["/tmp/a"]


# ---- run_gc mit Squash-Branches ----

def test_run_gc_zaehlt_squash_branches_in_derselben_zeile(tmp_path, capsys):
    claims = tmp_path / "claims"
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(cmd):
        if cmd[3] == "branch" and "--merged" in cmd:
            return _ok(cmd, "* main\n")
        if cmd[3] == "for-each-ref":
            return _ok(cmd, "main\nfeature/fertig\n")
        if cmd[3] == "merge-base":
            return _ok(cmd, "basis\n")
        if cmd[3] == "diff" and "--name-only" in cmd:
            return _ok(cmd, "a.txt\n")
        if cmd[3] == "diff" and "--quiet" in cmd:
            return _ok(cmd)
        return _ok(cmd)

    stats = gc.run_gc(claims, [repo], run=fake_run)
    assert stats == {"claims": 0, "branches": 1, "repos": 1}
    zeilen = [z for z in capsys.readouterr().out.splitlines() if "gemergte Branches" in z]
    assert len(zeilen) == 1 and "feature/fertig" in zeilen[0]


# ---- verwaiste Agenten-Sitzungen ----

def _fake_proc(basis, pid, name, *, start_ts, cpu_s=0.0, cwd="/home/dev"):
    """Legt ein Prozessverzeichnis an, wie es /proc liefert."""
    ordner = basis / str(pid)
    ordner.mkdir()
    (ordner / "comm").write_text(name + "\n")
    ticks = os.sysconf("SC_CLK_TCK")
    halbe = int(cpu_s * ticks / 2)
    (ordner / "stat").write_text(
        f"{pid} ({name}) S 1 1 1 0 -1 4194560 100 0 0 0 {halbe} {halbe} 0 0 20 0 1\n"
    )
    os.symlink(cwd, ordner / "cwd")
    os.utime(ordner, (start_ts, start_ts))
    return ordner


def test_cpu_sekunden_liest_utime_und_stime(tmp_path):
    ticks = os.sysconf("SC_CLK_TCK")
    text = f"7 (agy) S 1 1 1 0 -1 4194560 1 0 0 0 {int(ticks * 2)} {int(ticks * 3)} 0 0 20 0 1\n"
    assert gc._cpu_sekunden(text, ticks) == 5.0


def test_cpu_sekunden_vertraegt_kaputten_text():
    assert gc._cpu_sekunden("kein-klammer-stat", 100.0) == 0.0
    assert gc._cpu_sekunden("7 (agy) S 1", 100.0) == 0.0


def test_lese_agenten_prozesse_nimmt_nur_passende_namen(tmp_path):
    alt = 1_700_000_000.0
    _fake_proc(tmp_path, 101, "agy", start_ts=alt, cpu_s=2.0, cwd="/home/dev/a")
    _fake_proc(tmp_path, 102, "bash", start_ts=alt, cpu_s=1.0)
    _fake_proc(tmp_path, 103, "agy", start_ts=alt, cpu_s=3.0, cwd="/home/dev/b")

    treffer = gc.lese_agenten_prozesse(tmp_path, uid=None)

    assert [p["pid"] for p in treffer] == [101, 103]
    assert all(p["name"] == "agy" for p in treffer)
    assert treffer[0]["cwd"] == "/home/dev/a"
    assert treffer[0]["start"] == alt
    assert treffer[1]["cpu_s"] == 3.0


def test_lese_agenten_prozesse_filtert_nach_benutzer(tmp_path):
    _fake_proc(tmp_path, 201, "agy", start_ts=1_700_000_000.0)
    eigen = os.stat(tmp_path).st_uid

    assert [p["pid"] for p in gc.lese_agenten_prozesse(tmp_path, uid=eigen)] == [201]
    assert gc.lese_agenten_prozesse(tmp_path, uid=eigen + 1) == []


def test_lese_agenten_prozesse_ueberspringt_kaputte_eintraege(tmp_path):
    # Ohne lesbare stat-Datei ist die Rechenzeit unbekannt — solche Prozesse werden nicht
    # aufgenommen, damit niemand sie versehentlich als untätig einstuft.
    kaputt = tmp_path / "301"
    kaputt.mkdir()
    (kaputt / "comm").write_text("agy\n")
    (tmp_path / "kein-prozess.txt").write_text("x")

    assert gc.lese_agenten_prozesse(tmp_path, uid=None) == []


def _prozess(pid, alter_s, cpu_s, cwd="/home/dev/a", name="agy", now=2_000_000_000.0):
    return {"pid": pid, "name": name, "start": now - alter_s, "cpu_s": cpu_s, "cwd": cwd}


def test_verwaiste_prozesse_erkennt_dauerlaeufer():
    now = 2_000_000_000.0
    prozesse = [
        # Der echte Fall vom 25.11.: 20 Tage alt, 41 Minuten Rechenzeit = 0,14 % Auslastung.
        _prozess(1, alter_s=20 * 86400, cpu_s=41 * 60, now=now),
        # 3 Tage alt, 50 Minuten Rechenzeit = 1,2 %: arbeitet noch, bleibt stehen.
        _prozess(2, alter_s=3 * 86400, cpu_s=3000, now=now),
        _prozess(3, alter_s=3600, cpu_s=0, now=now),
    ]

    kandidaten = gc.verwaiste_agenten_prozesse(prozesse, now=now)

    assert [p["pid"] for p in kandidaten] == [1]


def test_verwaiste_prozesse_schont_laufende_reviews():
    now = 2_000_000_000.0
    prozesse = [
        _prozess(4, alter_s=30 * 86400, cpu_s=10, cwd="/home/dev/agy-review-in/pr462", now=now),
        _prozess(5, alter_s=30 * 86400, cpu_s=10, cwd="/home/dev/orca/workspaces/x", now=now),
    ]

    kandidaten = gc.verwaiste_agenten_prozesse(prozesse, now=now)

    assert [p["pid"] for p in kandidaten] == [5]


def test_verwaiste_prozesse_ignoriert_kaputte_datensaetze():
    now = 2_000_000_000.0
    prozesse = [
        {"pid": 6, "start": "keine-zahl", "cpu_s": 1.0},
        {"pid": 7, "start": now - 20 * 86400, "cpu_s": None, "name": "agy"},
    ]

    kandidaten = gc.verwaiste_agenten_prozesse(prozesse, now=now)

    assert [p["pid"] for p in kandidaten] == [7]


def test_beende_verwaiste_agenten_sendet_sigterm_und_ueberspringt_tote():
    signale = []

    def fake_kill(pid, nummer):
        if pid == 9:
            raise ProcessLookupError("weg")
        signale.append((pid, nummer))

    kandidaten = [{"pid": 8}, {"pid": 9}, {"pid": 10}]

    beendet = gc.beende_verwaiste_agenten(kandidaten, kill=fake_kill)

    assert beendet == [8, 10]
    assert signale == [(8, gc.signal.SIGTERM), (10, gc.signal.SIGTERM)]


def test_prozess_lesen_und_filter_passen_zusammen(tmp_path):
    alt = 1_600_000_000.0
    _fake_proc(tmp_path, 401, "agy", start_ts=alt, cpu_s=5.0, cwd="/home/dev/orca")
    _fake_proc(tmp_path, 402, "agy", start_ts=alt, cpu_s=1.0, cwd="/home/dev/a")

    gelesen = gc.lese_agenten_prozesse(tmp_path, uid=None)
    kandidaten = gc.verwaiste_agenten_prozesse(gelesen, now=alt + 10 * 86400)

    assert [p["pid"] for p in kandidaten] == [401, 402]


def test_lese_agenten_prozesse_filtert_standardmaessig_auf_den_agenten_benutzer():
    import inspect

    vorgabe = inspect.signature(gc.lese_agenten_prozesse).parameters["uid"].default

    assert vorgabe == gc.AGENT_PROZESS_UID


def test_beende_agenten_ueberspringt_pid_null_und_negativ():
    signale = []

    beendet = gc.beende_verwaiste_agenten(
        [{"pid": 0}, {"pid": -1}, {"pid": 42}], kill=lambda pid, nummer: signale.append(pid)
    )

    assert beendet == [42]
    assert signale == [42]


def test_beende_agenten_sendet_nie_sigkill():
    import inspect

    assert "signalnummer" not in inspect.signature(gc.beende_verwaiste_agenten).parameters


def test_raeume_agenten_sitzungen_trocken_beendet_nichts(tmp_path):
    alt = 1_600_000_000.0
    _fake_proc(tmp_path, 501, "agy", start_ts=alt, cpu_s=3.0, cwd="/home/dev/orca")
    signale = []

    zeilen = gc.raeume_agenten_sitzungen(
        now=alt + 10 * 86400, trocken=True, uid=None, proc_dir=tmp_path,
        kill=lambda pid, nummer: signale.append(pid),
    )

    assert signale == []
    assert len(zeilen) == 1
    assert zeilen[0].startswith("gc (trocken): verwaiste agy-Sitzung PID 501")
    assert "10 Tagen" in zeilen[0]
    assert "/home/dev/orca" in zeilen[0]


def test_raeume_agenten_sitzungen_beendet_und_meldet(tmp_path):
    alt = 1_600_000_000.0
    _fake_proc(tmp_path, 502, "agy", start_ts=alt, cpu_s=3.0, cwd="/home/dev/orca")
    _fake_proc(tmp_path, 503, "agy", start_ts=alt + 10 * 86400, cpu_s=0.5, cwd="/home/dev")
    signale = []

    zeilen = gc.raeume_agenten_sitzungen(
        now=alt + 10 * 86400, uid=None, proc_dir=tmp_path,
        kill=lambda pid, nummer: signale.append((pid, nummer)),
    )

    assert signale == [(502, gc.signal.SIGTERM)]
    assert len(zeilen) == 1
    assert zeilen[0].startswith("⚠ Stephan: verwaiste agy-Sitzung beendet (PID 502")
    assert "10 Tagen" in zeilen[0]


def test_raeume_agenten_sitzungen_vertraegt_unvollstaendige_datensaetze(tmp_path):
    zeilen = gc.raeume_agenten_sitzungen(
        now=2_000_000_000.0, trocken=True, uid=None, proc_dir=tmp_path / "gibt-es-nicht"
    )

    assert zeilen == []


def test_prozesse_aufraeumen_mit_trocken_schalter(monkeypatch, capsys):
    monkeypatch.setattr(gc, "raeume_agenten_sitzungen",
                        lambda trocken=False: [f"gc (trocken={trocken}): Zeile"])

    assert gc.prozesse_aufraeumen(trocken=True) == 0
    assert "trocken=True" in capsys.readouterr().out


def test_main_nur_prozesse_ueberspringt_git_teil(monkeypatch, capsys, tmp_path):
    # Auf CT 106 gibt es weder Projektliste noch Claims: der Schalter darf nichts davon anfassen.
    monkeypatch.setattr(gc, "raeume_agenten_sitzungen", lambda trocken=False: ["⚠ Stephan: Zeile"])
    monkeypatch.setattr(gc, "run_gc", lambda *a, **k: (_ for _ in ()).throw(AssertionError("darf nicht laufen")))
    monkeypatch.setattr(gc, "OPENCODE_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(gc, "OPENCODE_LOGS_DIR", str(tmp_path / "logs"))

    assert gc.main(["--nur-prozesse"]) == 0
    assert "⚠ Stephan: Zeile" in capsys.readouterr().out


# ---- prune_opencode_runs (Wegwerf-Klone der OpenCode-Läufe) ----

def _klon(runs, name, mtime):
    pfad = runs / name
    pfad.mkdir()
    (pfad / "datei").write_text("x", encoding="utf-8")
    os.utime(pfad, (mtime, mtime))
    return pfad


def test_prune_opencode_runs_entfernt_alte_klone_und_behaelt_junge(tmp_path):
    runs = tmp_path / "opencode-runs"
    runs.mkdir()
    alt = _klon(runs, "home-infra-20260101-000000", 0.0)
    jung = _klon(runs, "home-infra-20261127-000000", 900.0)

    stats = gc.prune_opencode_runs(runs, now=1000.0, clone_max_age_seconds=500.0,
                                   datei_max_age_seconds=500.0)
    assert stats == {"klone": 1, "patches": 0, "logs": 0}
    assert not alt.exists()
    assert jung.is_dir()


def test_prune_opencode_runs_haelt_patches_und_logs_laenger(tmp_path):
    runs = tmp_path / "runs"
    logs = tmp_path / "logs"
    runs.mkdir()
    logs.mkdir()
    alt_patch = runs / "a.patch"
    neu_patch = runs / "b.patch"
    alt_log = logs / "a.jsonl"
    neu_log = logs / "b.jsonl"
    fremd = logs / "notiz.txt"
    for datei in (alt_patch, neu_patch, alt_log, neu_log, fremd):
        datei.write_text("x", encoding="utf-8")
    for datei in (alt_patch, alt_log, fremd):
        os.utime(datei, (0.0, 0.0))
    for datei in (neu_patch, neu_log):
        os.utime(datei, (900.0, 900.0))

    stats = gc.prune_opencode_runs(runs, logs, clone_max_age_seconds=100.0,
                                   datei_max_age_seconds=500.0, now=1000.0)
    assert stats == {"klone": 0, "patches": 1, "logs": 1}
    assert not alt_patch.exists() and neu_patch.exists()
    assert not alt_log.exists() and neu_log.exists()
    assert fremd.exists()


def test_prune_opencode_runs_fehlende_verzeichnisse_sind_kein_fehler(tmp_path):
    assert gc.prune_opencode_runs(tmp_path / "keine-klone", tmp_path / "keine-logs") == {
        "klone": 0, "patches": 0, "logs": 0}


def test_prozesse_aufraeumen_raeumt_alte_opencode_klone_auf(tmp_path, monkeypatch, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    alt = _klon(runs, "home-infra-20260101-000000", 0.0)     # 1970: älter als jede Vorgabe
    monkeypatch.setattr(gc, "raeume_agenten_sitzungen", lambda trocken=False: [])
    monkeypatch.setattr(gc, "OPENCODE_RUNS_DIR", str(runs))
    monkeypatch.setattr(gc, "OPENCODE_LOGS_DIR", str(tmp_path / "logs"))

    assert gc.prozesse_aufraeumen(trocken=False) == 0
    assert not alt.exists()
    assert "opencode-runs: 1 Klone" in capsys.readouterr().out


def test_prozesse_aufraeumen_trocken_laesst_die_klone_stehen(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    alt = _klon(runs, "home-infra-20260101-000000", 0.0)
    monkeypatch.setattr(gc, "raeume_agenten_sitzungen", lambda trocken=False: [])
    monkeypatch.setattr(gc, "OPENCODE_RUNS_DIR", str(runs))
    monkeypatch.setattr(gc, "OPENCODE_LOGS_DIR", str(tmp_path / "logs"))

    assert gc.prozesse_aufraeumen(trocken=True) == 0
    assert alt.is_dir()
