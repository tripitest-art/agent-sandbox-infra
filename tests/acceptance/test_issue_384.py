"""Akzeptanztest: Der Takt postet die Rückfrage des Agenten selbst (Agent hat kein gh mehr)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_frage", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_frage"] = r
_spec.loader.exec_module(r)


@pytest.fixture
def frage_datei(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "WORKDIR", str(tmp_path))
    (tmp_path / ".git").mkdir()
    return tmp_path / ".git" / "frage.md"


def _gh_fake(aufrufe, fehler=None):
    def gh(*args, **kw):
        aufrufe.append(list(args))
        if fehler:
            raise fehler
        return None
    return gh


def test_postet_die_rueckfrage_und_loescht_die_datei(monkeypatch, frage_datei):
    aufrufe = []
    monkeypatch.setattr(r, "gh", _gh_fake(aufrufe))
    frage_datei.write_text("Der Akzeptanztest passt nicht zum Ziel, weil …\n", encoding="utf-8")

    assert r.frage_des_agenten_posten(7) is True
    assert len(aufrufe) == 1, aufrufe
    assert aufrufe[0][:4] == ["issue", "comment", "7", "--body-file"]
    assert not frage_datei.exists()


def test_ohne_datei_kein_aufruf(monkeypatch, frage_datei):
    aufrufe = []
    monkeypatch.setattr(r, "gh", _gh_fake(aufrufe))
    assert r.frage_des_agenten_posten(7) is False
    assert aufrufe == []


def test_leere_datei_wird_nicht_gepostet(monkeypatch, frage_datei):
    aufrufe = []
    monkeypatch.setattr(r, "gh", _gh_fake(aufrufe))
    frage_datei.write_text("   \n\n\t\n", encoding="utf-8")
    assert r.frage_des_agenten_posten(7) is False
    assert aufrufe == []


def test_fehlschlag_laesst_die_datei_liegen_und_wirft_nicht(monkeypatch, frage_datei):
    aufrufe = []
    monkeypatch.setattr(r, "gh", _gh_fake(aufrufe, fehler=RuntimeError("kein Netz")))
    frage_datei.write_text("Frage\n", encoding="utf-8")

    assert r.frage_des_agenten_posten(7) is False
    assert frage_datei.exists()
