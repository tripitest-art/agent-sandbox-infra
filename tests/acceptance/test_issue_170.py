import importlib.util
import os
import sys
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _load():
    spec = importlib.util.spec_from_file_location("worker_tick_claims", _SANDBOX / "worker_tick.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["worker_tick_claims"] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load()

JETZT = 1_800_000_000.0
TAG = 86400.0


def _datei(ordner, name, alter_tage, inhalt=""):
    pfad = ordner / name
    pfad.write_text(inhalt, encoding="utf-8")
    zeit = JETZT - alter_tage * TAG
    os.utime(pfad, (zeit, zeit))
    return pfad


def test_vorgabe_ist_dreissig_tage():
    assert w.CLAIM_MAX_AGE_DAYS == 30


def test_alte_erledigt_marken_gehen_junge_bleiben(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path))
    alt = _datei(tmp_path, "pr1_x.done", 60, "11")
    jung = _datei(tmp_path, "pr2_y.done", 5, "11")
    assert w.cleanup_claims(now=JETZT) == 1
    assert not alt.exists() and jung.exists()


def test_verwaiste_alte_belegung_geht_lebende_und_junge_bleiben(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path))
    monkeypatch.setattr(w, "_pid_alive", lambda pid: pid == 4242)
    tot_alt = _datei(tmp_path, "issue3.claim", 40, "999")
    unlesbar_alt = _datei(tmp_path, "issue4.claim", 40, "kein-pid")
    lebt_alt = _datei(tmp_path, "issue5.claim", 40, "4242")
    tot_jung = _datei(tmp_path, "issue6.claim", 1, "999")
    assert w.cleanup_claims(now=JETZT) == 2
    assert not tot_alt.exists() and not unlesbar_alt.exists()
    assert lebt_alt.exists() and tot_jung.exists()


def test_fremdes_und_verzeichnisse_bleiben(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path))
    notiz = _datei(tmp_path, "notiz.txt", 400, "x")
    ordner = tmp_path / "unter.done"
    ordner.mkdir()
    zeit = JETZT - 400 * TAG
    os.utime(ordner, (zeit, zeit))
    assert w.cleanup_claims(now=JETZT) == 0
    assert notiz.exists() and ordner.is_dir()


def test_alter_ist_einstellbar(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path))
    datei = _datei(tmp_path, "pr9_z.done", 3, "1")
    assert w.cleanup_claims(max_age_days=7, now=JETZT) == 0 and datei.exists()
    assert w.cleanup_claims(max_age_days=2, now=JETZT) == 1 and not datei.exists()


def test_fehlendes_verzeichnis_wird_nicht_angelegt(monkeypatch, tmp_path):
    ziel = tmp_path / "gibt-es-nicht"
    monkeypatch.setattr(w, "CLAIM_DIR", str(ziel))
    assert w.cleanup_claims(now=JETZT) == 0
    assert not ziel.exists()


def test_fehler_beim_loeschen_zaehlt_nicht_und_bricht_nicht_ab(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path))
    kaputt = _datei(tmp_path, "a.done", 90, "1")
    gut = _datei(tmp_path, "b.done", 90, "1")
    echtes_remove = os.remove

    def remove(pfad, *a, **k):
        if str(pfad).endswith("a.done"):
            raise PermissionError("gesperrt")
        return echtes_remove(pfad, *a, **k)

    monkeypatch.setattr(w.os, "remove", remove)
    monkeypatch.setattr(w.os, "unlink", remove)
    assert w.cleanup_claims(now=JETZT) == 1
    assert kaputt.exists() and not gut.exists()
