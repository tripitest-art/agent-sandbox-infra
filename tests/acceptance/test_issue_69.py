import importlib.util
import sys
import subprocess
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_status", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_status"] = r
_spec.loader.exec_module(r)

SHA = "b" * 40


def test_review_status_je_urteil():
    assert r.review_status("**Freigegeben**") == ("success", "Review: Freigegeben")
    assert r.review_status("**Änderungen nötig**") == ("failure", "Review: Änderungen nötig")
    assert r.review_status("Vielleicht") is None


def test_post_review_status_ruft_gh_api(monkeypatch):
    calls = []
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: calls.append((a, k)) or "")
    assert r.post_review_status(SHA, "**Freigegeben**") is True
    (args, kw), = calls
    assert args[0] == "api" and args[1] == f"repos/{r.REPO}/statuses/{SHA}"
    assert kw.get("repo") is False
    assert "state=success" in args and "context=review" in args


def test_post_review_status_ohne_urteil_ruft_nichts_auf(monkeypatch):
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein Aufruf")))
    assert r.post_review_status(SHA, "Vielleicht") is False


def test_fehler_wird_gemeldet_nicht_geworfen(monkeypatch, capsys):
    def kaputt(*a, **k):
        raise subprocess.CalledProcessError(1, "gh", stderr="geheim-im-fehlertext")
    monkeypatch.setattr(r, "gh_text", kaputt)
    assert r.post_review_status(SHA, "**Freigegeben**") is False
    out = capsys.readouterr().out
    assert "⚠ Stephan:" in out and SHA[:7] in out and "geheim-im-fehlertext" not in out
