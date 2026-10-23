import importlib.util
import os
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_datei_stichprobe", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_datei_stichprobe"] = w
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
    """Urteilt als fremder Nutzer, nicht als der Testprozess.

    Die Testdateien gehören dem Testprozess; damit entscheiden die Sonstige-Bits, egal ob
    pytest als `worker` oder als root läuft. Ein Zielnutzer aus `os.getuid()` wäre im
    pre-push-Hook 0, und `uid == 0` umgeht die Modusbits (CAP_DAC_OVERRIDE) — der Test
    wäre dort grün, wo er rot sein muss."""
    monkeypatch.setattr(w, "pwd", _Pwd(FREMDE_UID, FREMDE_UID))
    monkeypatch.setattr(w.os, "getgrouplist", lambda user, gid: [gid], raising=False)


def _durchquerbar(pfad: Path) -> None:
    """Elternordner für den fremden Nutzer durchquerbar machen (nur x-Bit ergänzen).

    pytest legt seine Temp-Ordner mit 0700 an; für FREMDE_UID fehlte das x-Bit und der
    Ordner-Check schlüge vor der Datei-Stichprobe fehl. Es wird nichts lesbar geöffnet."""
    for eltern in (pfad, *pfad.parents):
        if str(eltern) == "/tmp":
            break
        os.chmod(eltern, os.stat(eltern).st_mode | 0o011)


def test_sandbox_health_check_stichprobe_findet_unbeschreibbare_datei(monkeypatch, tmp_path, capsys):
    baum = tmp_path / "wt"
    baum.mkdir()
    os.chmod(baum, 0o777)                    # für den fremden Nutzer betret- und schreibbar
    frei = baum / "frei.txt"
    frei.write_text("in Ordnung", encoding="utf-8")
    os.chmod(frei, 0o646)                    # Eigentümer schreibt, Sonstige auch
    _durchquerbar(tmp_path)
    _zielnutzer(monkeypatch)
    assert w.sandbox_health_check(baum, "worker") is True

    gesperrt = baum / "worker_tick.py"
    gesperrt.write_text("root", encoding="utf-8")
    os.chmod(gesperrt, 0o444)                # Sonstige-Bits ohne w
    assert w.sandbox_health_check(baum, "worker") is False
    out = capsys.readouterr().out
    assert "⚠ Stephan:" in out and "nicht schreibbar" in out


def test_sandbox_health_check_stichprobe_prueft_auch_das_slot_admin_verzeichnis(monkeypatch, tmp_path, capsys):
    klon = tmp_path / "klon"
    admin = klon / ".git" / "worktrees" / "w2"
    admin.mkdir(parents=True)
    index = admin / "index"
    index.write_text("root", encoding="utf-8")
    os.chmod(index, 0o444)
    wt = tmp_path / "worktrees" / "w2"
    wt.mkdir(parents=True)
    os.chmod(wt, 0o777)
    _durchquerbar(tmp_path)
    monkeypatch.setattr(w, "BASE_WORKDIR", str(klon))
    monkeypatch.setattr(w, "WORKDIR", str(wt))
    _zielnutzer(monkeypatch)
    assert w.sandbox_health_check(wt, "worker") is False
    assert "nicht schreibbar" in capsys.readouterr().out


def test_sandbox_health_check_stichprobe_gruen_wenn_alles_schreibbar(monkeypatch, tmp_path):
    klon = tmp_path / "klon"
    admin = klon / ".git" / "worktrees" / "w2"
    admin.mkdir(parents=True)
    (admin / "index").write_text("ok", encoding="utf-8")
    os.chmod(admin / "index", 0o646)
    wt = tmp_path / "worktrees" / "w2"
    (wt / "sandbox").mkdir(parents=True)
    datei = wt / "sandbox" / "worker_tick.py"
    datei.write_text("ok", encoding="utf-8")
    os.chmod(datei, 0o646)
    os.chmod(wt, 0o777)
    _durchquerbar(tmp_path)
    monkeypatch.setattr(w, "BASE_WORKDIR", str(klon))
    monkeypatch.setattr(w, "WORKDIR", str(wt))
    _zielnutzer(monkeypatch)
    assert w.sandbox_health_check(wt, "worker") is True
