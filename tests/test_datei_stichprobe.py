"""Unit-Tests zur Datei-Stichprobe im Sandbox-Check (#317).

Die Stichprobe entscheidet über die Modusbits eines **fremden** Nutzers, nicht über die des
Testprozesses: der pre-push-Hook führt `TEST_CMD` als root aus (uid 0 umgeht die Bits), der Agent
läuft als `worker`. Deshalb wird `pwd` mit einer fremden uid gefaked und die Erwartung hängt an den
Sonstige-Bits; die Elternordner werden für diesen Nutzer nur durchquerbar gemacht.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_datei_stichprobe_unit", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_datei_stichprobe_unit"] = w
_spec.loader.exec_module(w)

FREMDE_UID = 424242      # weder root noch Eigentümer der Testdateien


class _Eintrag:
    def __init__(self, uid, gid):
        self.pw_uid, self.pw_gid, self.pw_dir = uid, gid, "/home/worker"


class _Pwd:
    def __init__(self, uid, gid):
        self._uid, self._gid = uid, gid

    def getpwnam(self, user):
        return _Eintrag(self._uid, self._gid)


def _zielnutzer(monkeypatch):
    monkeypatch.setattr(w, "pwd", _Pwd(FREMDE_UID, FREMDE_UID))
    monkeypatch.setattr(w.os, "getgrouplist", lambda user, gid: [gid], raising=False)


def _durchquerbar(pfad: Path) -> None:
    """Elternordner für den fremden Nutzer durchquerbar machen (nur x-Bit ergänzen)."""
    for eltern in (pfad, *pfad.parents):
        if str(eltern) == "/tmp":
            break
        os.chmod(eltern, os.stat(eltern).st_mode | 0o011)


def _ohne_admin(monkeypatch, tmp_path):
    """Kein Slot-Admin-Verzeichnis im Klon: dann prüft der Check nur den Arbeitsbaum."""
    monkeypatch.setattr(w, "BASE_WORKDIR", str(tmp_path / "kein-klon"))
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))


@pytest.mark.parametrize("modus, in_gruppe, erwartet", [
    (0o600, False, False),   # Eigentümer schreibt, der fremde Nutzer aber nicht
    (0o400, False, False),   # niemand außer dem Eigentümer, und der nur lesend
    (0o040, True, False),    # Gruppe nur lesend
    (0o020, True, True),     # Gruppe schreibt
    (0o010, True, False),    # Gruppe nur x, kein w
    (0o006, False, True),    # Sonstige schreiben
    (0o004, False, False),   # Sonstige nur lesend
    (0o444, False, False),   # lesbar, aber nicht schreibbar – der Fall aus #280
    (0o646, False, True),    # normale schreibbare Datei ohne x-Bit
    (0o644, False, False),   # gewöhnliche Repo-Datei: für Sonstige nur lesbar
])
def test_datei_schreibbar_wertet_die_bits_des_kernels_aus(tmp_path, modus, in_gruppe, erwartet):
    datei = tmp_path / "datei"
    datei.write_text("x", encoding="utf-8")
    os.chmod(datei, modus)
    st = datei.stat()
    gids = [st.st_gid] if in_gruppe else []
    assert w._datei_schreibbar(datei, FREMDE_UID, gids) is erwartet


@pytest.mark.parametrize("modus", [0o600, 0o700])
def test_datei_schreibbar_nimmt_die_eigentuemerbits_fuer_den_eigentuemer(tmp_path, modus):
    # Nur das w-Bit zählt; 0o700 belegt, dass das x-Bit für Dateien keine Rolle spielt.
    datei = tmp_path / "datei"
    datei.write_text("x", encoding="utf-8")
    os.chmod(datei, modus)
    assert w._datei_schreibbar(datei, datei.stat().st_uid, []) is True


def test_datei_schreibbar_laesst_root_die_bits_umgehen(tmp_path):
    datei = tmp_path / "datei"
    datei.write_text("x", encoding="utf-8")
    os.chmod(datei, 0o000)
    assert w._datei_schreibbar(datei, 0, []) is True


def test_datei_schreibbar_meldet_fehlende_datei_als_nicht_schreibbar(tmp_path):
    assert w._datei_schreibbar(tmp_path / "gibt-es-nicht", FREMDE_UID, []) is False


def test_stichprobe_findet_nicht_schreibbare_datei_in_der_tiefe(monkeypatch, tmp_path, capsys):
    baum = tmp_path / "wt"
    tief = baum / "sandbox" / "paket" / "teil"
    tief.mkdir(parents=True)
    for ordner in (baum, baum / "sandbox", baum / "sandbox" / "paket", tief):
        os.chmod(ordner, 0o777)
    frei = baum / "README.md"
    frei.write_text("ok", encoding="utf-8")
    os.chmod(frei, 0o646)
    gesperrt = tief / "worker_tick.py"
    gesperrt.write_text("root", encoding="utf-8")
    os.chmod(gesperrt, 0o444)
    _durchquerbar(tmp_path)
    _ohne_admin(monkeypatch, tmp_path)
    _zielnutzer(monkeypatch)

    assert w.sandbox_health_check(baum, "worker") is False
    out = capsys.readouterr().out
    assert "⚠ Stephan: Sandbox-Check:" in out
    assert f"{gesperrt} ist für Nutzer worker nicht schreibbar." in out


def test_stichprobe_prueft_die_git_datei_des_slot_worktrees(monkeypatch, tmp_path):
    # Im Slot-Worktree ist `.git` eine Datei (gitdir-Zeiger). Gehört sie root, kann git dort
    # nichts schreiben – genau der Fehlgriff aus #280, nur eine Ebene höher.
    baum = tmp_path / "wt"
    baum.mkdir()
    os.chmod(baum, 0o777)
    gitdatei = baum / ".git"
    gitdatei.write_text("gitdir: /root/work/agent-sandbox-infra/.git/worktrees/w2\n", encoding="utf-8")
    os.chmod(gitdatei, 0o444)
    _durchquerbar(tmp_path)
    _ohne_admin(monkeypatch, tmp_path)
    _zielnutzer(monkeypatch)
    assert w.sandbox_health_check(baum, "worker") is False


def test_stichprobe_steigt_nicht_in_ein_git_verzeichnis(monkeypatch, tmp_path, capsys):
    # Im normalen Klon ist `.git` ein Verzeichnis voller root-eigener Verwaltungsdateien; die
    # gehören geprüft, aber nicht hier – im Slot-Worktree liegt das Admin-Verzeichnis ohnehin
    # unter BASE_WORKDIR/.git/worktrees/<slot>.
    baum = tmp_path / "wt"
    innen = baum / ".git" / "objects"
    innen.mkdir(parents=True)
    (innen / "pak").write_text("root", encoding="utf-8")
    os.chmod(innen / "pak", 0o444)
    frei = baum / "datei.txt"
    frei.write_text("ok", encoding="utf-8")
    os.chmod(frei, 0o646)
    os.chmod(baum, 0o777)
    _durchquerbar(tmp_path)
    _ohne_admin(monkeypatch, tmp_path)
    _zielnutzer(monkeypatch)
    assert w.sandbox_health_check(baum, "worker") is True
    assert "nicht schreibbar" not in capsys.readouterr().out


def test_stichprobe_bleibt_bei_hoechstens_max_files(monkeypatch, tmp_path):
    # Die Stichprobe ist eine Frühwarnung, kein Vollaudit: hinter der Obergrenze wird nichts mehr
    # geprüft, damit der Check vor dem Claim nicht über tausende Dateien läuft.
    baum = tmp_path / "wt"
    baum.mkdir()
    os.chmod(baum, 0o777)
    for name in ("a.txt", "b.txt"):
        datei = baum / name
        datei.write_text("ok", encoding="utf-8")
        os.chmod(datei, 0o646)
    gesperrt = baum / "c.txt"
    gesperrt.write_text("root", encoding="utf-8")
    os.chmod(gesperrt, 0o444)
    monkeypatch.setattr(w, "HEALTH_CHECK_MAX_FILES", 2)
    _durchquerbar(tmp_path)
    _ohne_admin(monkeypatch, tmp_path)
    _zielnutzer(monkeypatch)
    assert w.sandbox_health_check(baum, "worker") is True


def test_stichprobe_ueberspringt_nicht_statbares_admin_verzeichnis(monkeypatch, tmp_path):
    # Im CI-Läufer ist `/root` für den Laufnutzer nicht durchquerbar; `is_dir()` auf
    # `<klon>/.git/worktrees/<slot>` wirft dann PermissionError. Die Stichprobe darf dort
    # auslassen, statt den ganzen Check mit einer Ausnahme zu beenden.
    baum = tmp_path / "ok"
    baum.mkdir()
    os.chmod(baum, 0o777)
    nicht_statbar = Path(w.BASE_WORKDIR) / ".git" / "worktrees" / "ok"
    echtes_is_dir = Path.is_dir

    def is_dir(self):
        if self == nicht_statbar:
            raise PermissionError(13, "Permission denied", str(self))
        return echtes_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    _durchquerbar(tmp_path)
    _zielnutzer(monkeypatch)
    assert w.sandbox_health_check(baum, "worker") is True


def test_max_files_ist_die_obergrenze_der_stichprobe():
    assert w.HEALTH_CHECK_MAX_FILES == 20
