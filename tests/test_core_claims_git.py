"""Tests für `sandbox/core`: claims (Belegung) und git_ops (Git-Helfer) – Issue #220.

Ohne Netzwerk und ohne echtes Repository: Der Git-Aufruf kommt als `run` herein und wird durch
einen Fake ersetzt, Belegungen liegen in `tmp_path`. Tote PIDs entstehen durch einen beendeten
Kindprozess, fremde Besitzer durch eine abweichende `pid`-Angabe.
"""

import os
import subprocess
import sys

import pytest

from sandbox.core import claims, git_ops


def _toter_pid() -> int:
    """PID eines sicher beendeten Prozesses."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# ---- claims.claim_job / release_job ----

def test_claim_job_legt_datei_mit_der_eigenen_pid_an_und_release_loescht_sie(tmp_path):
    belegung = tmp_path / "claims"
    assert claims.claim_job("issue59", belegung) is True
    datei = belegung / "issue59.claim"
    assert datei.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert claims.release_job("issue59", belegung) is True
    assert not datei.exists()


def test_claim_job_meldet_belegung_eines_lebenden_fremden_prozesses(tmp_path):
    """Fremd und lebend: nicht übernehmen, die fremde PID bleibt stehen."""
    belegung = tmp_path / "claims"
    belegung.mkdir()
    datei = belegung / "issue59.claim"
    datei.write_text(str(os.getpid()), encoding="utf-8")
    assert claims.claim_job("issue59", belegung, pid=os.getpid() + 1) is False
    assert datei.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_claim_job_uebernimmt_belegung_eines_beendeten_prozesses(tmp_path):
    """Ein abgebrochener Takt darf den Auftrag nicht für immer halten."""
    belegung = tmp_path / "claims"
    belegung.mkdir()
    datei = belegung / "issue59.claim"
    datei.write_text(str(_toter_pid()), encoding="utf-8")
    assert claims.claim_job("issue59", belegung, pid=4242) is True
    assert datei.read_text(encoding="utf-8").strip() == "4242"


@pytest.mark.parametrize("inhalt", ["kaputt", "", "  ", "-1"])
def test_claim_job_uebernimmt_unlesbare_belegung(tmp_path, inhalt):
    """Ohne lesbare PID schützt die Datei niemanden: sie gilt als verwaist."""
    belegung = tmp_path / "claims"
    belegung.mkdir()
    datei = belegung / "issue59.claim"
    datei.write_text(inhalt, encoding="utf-8")
    assert claims.claim_job("issue59", belegung, pid=4242) is True
    assert datei.read_text(encoding="utf-8").strip() == "4242"


def test_claim_job_ersetzt_pfadtrenner_im_schluessel(tmp_path):
    """Ein Schlüssel mit „/“ und „:“ wird ein Dateiname, kein Pfad darunter."""
    belegung = tmp_path / "claims"
    assert claims.claim_job("projekt/issue7@2026-10-15T10:35:45Z", belegung, pid=4242) is True
    assert [p.name for p in belegung.iterdir()] == [
        "projekt_issue7@2026-10-15T10_35_45Z.claim"]


def test_claim_job_meldet_nicht_nutzbares_verzeichnis_einmal(tmp_path, monkeypatch, capsys):
    """Regel 7: ohne Belegung einmal „⚠ Stephan:“, der Aufrufer läuft weiter (True)."""
    monkeypatch.setattr(claims, "_WARNED", set())
    datei = tmp_path / "claims"
    datei.write_text("kein Verzeichnis", encoding="utf-8")
    assert claims.claim_job("issue59", datei) is True
    fehler = capsys.readouterr().err
    assert fehler.count("⚠ Stephan:") == 1 and "nicht nutzbar" in fehler
    assert claims.claim_job("issue59", datei) is True
    assert capsys.readouterr().err == ""


def test_release_job_gibt_fremde_belegung_nicht_frei(tmp_path):
    belegung = tmp_path / "claims"
    belegung.mkdir()
    datei = belegung / "issue99.claim"
    datei.write_text("99999", encoding="utf-8")
    assert claims.release_job("issue99", belegung, pid=1234) is False
    assert datei.exists()


def test_release_job_ohne_belegung_ist_kein_fehler(tmp_path):
    assert claims.release_job("issue99", tmp_path / "claims") is True


# ---- claims.mark_job_done / job_done_anywhere ----

def test_mark_job_done_legt_verzeichnis_an_und_job_done_anywhere_sieht_es(tmp_path):
    belegung = tmp_path / "claims"
    assert claims.job_done_anywhere("issue59", belegung) is False
    claims.mark_job_done("issue59", belegung, pid=4242)
    assert claims.job_done_anywhere("issue59", belegung) is True
    assert (belegung / "issue59.done").read_text(encoding="utf-8").strip() == "4242"


def test_mark_job_done_meldet_schreibfehler_ohne_zu_werfen(tmp_path, capsys):
    datei = tmp_path / "claims"
    datei.write_text("kein Verzeichnis", encoding="utf-8")
    claims.mark_job_done("issue59", datei)
    assert "nicht merkbar" in capsys.readouterr().err


# ---- claims.cleanup_claims ----

def test_cleanup_loescht_junge_done_dateien_bei_max_age_null(tmp_path):
    """Direkt nach dem Lauf sind die eben geschriebenen Marken schon erledigt."""
    belegung = tmp_path / "claims"
    belegung.mkdir()
    marke = belegung / "alt.done"
    marke.write_text("1", encoding="utf-8")
    assert claims.cleanup_claims(belegung, max_age_days=0) == 1
    assert not marke.exists()


def test_cleanup_behaelt_frische_dateien_bei_vorgabe_alter(tmp_path):
    belegung = tmp_path / "claims"
    belegung.mkdir()
    (belegung / "frisch.done").write_text("1", encoding="utf-8")
    (belegung / "frisch.claim").write_text(str(os.getpid()), encoding="utf-8")
    assert claims.cleanup_claims(belegung) == 0
    assert sorted(p.name for p in belegung.iterdir()) == ["frisch.claim", "frisch.done"]


def test_cleanup_loescht_verwaiste_belegung_und_behaelt_fremde_dateien(tmp_path):
    belegung = tmp_path / "claims"
    belegung.mkdir()
    (belegung / "alt.claim").write_text(str(_toter_pid()), encoding="utf-8")
    (belegung / "notiz.txt").write_text("bleibt", encoding="utf-8")
    (belegung / "unterordner").mkdir()
    assert claims.cleanup_claims(belegung, max_age_days=0) == 1
    assert sorted(p.name for p in belegung.iterdir()) == ["notiz.txt", "unterordner"]


def test_cleanup_behaelt_belegung_eines_lebenden_prozesses(tmp_path):
    belegung = tmp_path / "claims"
    belegung.mkdir()
    datei = belegung / "laeuft.claim"
    datei.write_text(str(os.getpid()), encoding="utf-8")
    assert claims.cleanup_claims(belegung, max_age_days=0) == 0
    assert datei.exists()


def test_cleanup_ohne_verzeichnis_ist_null_und_legt_nichts_an(tmp_path):
    fehlend = tmp_path / "gibtsnicht"
    assert claims.cleanup_claims(fehlend, max_age_days=0) == 0
    assert not fehlend.exists()


# ---- claims._pid_alive ----

def test_pid_alive_erkennt_laufenden_und_beendeten_prozess():
    assert claims._pid_alive(os.getpid()) is True
    assert claims._pid_alive(_toter_pid()) is False


@pytest.mark.parametrize("pid", [None, 0, -1])
def test_pid_alive_ohne_brauchbare_pid_lebt_nicht(pid):
    assert claims._pid_alive(pid) is False


def test_pid_alive_linux_fragt_os_kill_mit_signal_null(monkeypatch):
    aufrufe: list[tuple[int, int]] = []
    monkeypatch.setattr(claims.sys, "platform", "linux")
    monkeypatch.setattr(claims.os, "kill", lambda pid, sig: aufrufe.append((pid, sig)))
    assert claims._pid_alive(4242) is True
    assert aufrufe == [(4242, 0)]

    def kein_prozess(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(claims.os, "kill", kein_prozess)
    assert claims._pid_alive(4242) is False


def test_pid_alive_gilt_bei_fehlendem_recht_als_lebend(monkeypatch):
    """Ein Prozess eines anderen Nutzers ist erreichbar, aber nicht prüfbar – er lebt."""

    def verweigert(pid, sig):
        raise PermissionError

    monkeypatch.setattr(claims.sys, "platform", "linux")
    monkeypatch.setattr(claims.os, "kill", verweigert)
    assert claims._pid_alive(4242) is True


class _FakeKernel32:
    """Minimaler Ersatz für kernel32; Handle, Exitcode und Fehlercode sind vorgegeben."""

    def __init__(self, handle=1, exit_code=259, error=0):
        self.handle, self.exit_code, self.error = handle, exit_code, error
        self.geoeffnet, self.geschlossen = [], []

    def OpenProcess(self, access, inherit, pid):
        self.geoeffnet.append(pid)
        return self.handle

    def GetExitCodeProcess(self, handle, ref):
        ref._obj.value = self.exit_code
        return 1

    def GetLastError(self):
        return self.error

    def CloseHandle(self, handle):
        self.geschlossen.append(handle)
        return 1


def _windows(monkeypatch, **kwargs) -> _FakeKernel32:
    """Schaltet den Windows-Zweig ein; os.kill darf dort nicht aufgerufen werden (#118)."""
    import ctypes
    import types

    k = _FakeKernel32(**kwargs)
    monkeypatch.setattr(claims.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=k), raising=False)
    monkeypatch.setattr(claims.os, "kill", lambda *a: pytest.fail("os.kill unter Windows"))
    return k


def test_pid_alive_windows_laufender_prozess_lebt_und_handle_wird_geschlossen(monkeypatch):
    k = _windows(monkeypatch, exit_code=259)
    assert claims._pid_alive(4242) is True
    assert k.geoeffnet == [4242] and k.geschlossen == [1]


def test_pid_alive_windows_beendeter_prozess_lebt_nicht(monkeypatch):
    k = _windows(monkeypatch, exit_code=0)
    assert claims._pid_alive(4242) is False
    assert k.geschlossen == [1]


def test_pid_alive_windows_zugriff_verweigert_gilt_als_lebend(monkeypatch):
    _windows(monkeypatch, handle=0, error=5)
    assert claims._pid_alive(4242) is True


def test_pid_alive_windows_unbekannte_pid_lebt_nicht(monkeypatch):
    k = _windows(monkeypatch, handle=0, error=87)
    assert claims._pid_alive(4242) is False
    assert k.geschlossen == []


# ---- git_ops ----

class FakeRun:
    """Ersetzt den Git-Aufruf: merkt die Argumente und liefert vorgegebene Exitcodes."""

    def __init__(self, codes: list[int] | None = None, stdout: str = "", stderr: str = ""):
        self.codes = list(codes or [])
        self.stdout, self.stderr = stdout, stderr
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        code = self.codes.pop(0) if self.codes else 0
        return subprocess.CompletedProcess(cmd, code, self.stdout, self.stderr)


def test_clean_worktree_setzt_hart_zurueck_und_raeumt_auf():
    run = FakeRun()
    assert git_ops.clean_worktree("/tmp/wd", run) is True
    assert run.calls == [
        ["git", "reset", "--hard", "HEAD"],
        ["git", "clean", "-fd"],
    ]


def test_clean_worktree_raeumt_auch_nach_gescheitertem_reset_auf():
    """Ein Rest von `reset` darf `clean` nicht verhindern; der Fehler bleibt sichtbar."""
    run = FakeRun(codes=[1, 0])
    assert git_ops.clean_worktree("/tmp/wd", run) is False
    assert len(run.calls) == 2


def test_clean_worktree_meldet_auch_fehler_von_clean():
    assert git_ops.clean_worktree("/tmp/wd", FakeRun(codes=[0, 1])) is False


def test_get_current_head_liefert_getrimmten_sha():
    run = FakeRun(stdout="  abc1234567\n")
    assert git_ops.get_current_head("/tmp/wd", run) == "abc1234567"
    assert run.calls == [["git", "rev-parse", "HEAD"]]


def test_get_current_head_wirft_runtimeerror_mit_verzeichnis_und_grund():
    run = FakeRun(codes=[128], stderr="fatal: not a git repository\n")
    with pytest.raises(RuntimeError) as fehler:
        git_ops.get_current_head("/tmp/wd", run)
    assert "/tmp/wd" in str(fehler.value)
    assert "not a git repository" in str(fehler.value)


def test_branch_exists_prueft_den_vollen_ref_pfad():
    run = FakeRun()
    assert git_ops.branch_exists("/tmp/wd", "feature/issue-220", run) is True
    assert run.calls == [
        ["git", "rev-parse", "--verify", "--quiet", "refs/heads/feature/issue-220"]]


def test_branch_exists_ist_false_bei_fehlercode():
    assert git_ops.branch_exists("/tmp/wd", "gibtsnicht", FakeRun(codes=[1])) is False
