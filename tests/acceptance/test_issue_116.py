import importlib.util
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_httpstatus", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_httpstatus"] = r
_spec.loader.exec_module(r)

SHA = "c" * 40


def _fehler(stderr):
    return subprocess.CalledProcessError(1, "gh", stderr=stderr)


def test_http_status_aus_gh_stderr():
    assert r.gh_http_status(_fehler("gh: Resource not accessible by personal access token (HTTP 403)")) == 403
    assert r.gh_http_status(_fehler("gh: Not Found (HTTP 404)\n")) == 404


def test_http_status_none_ohne_beleg():
    assert r.gh_http_status(_fehler("irgendein Fehler")) is None
    assert r.gh_http_status(_fehler("")) is None
    assert r.gh_http_status(_fehler(None)) is None
    assert r.gh_http_status(_fehler("(HTTP 99)")) is None
    assert r.gh_http_status(_fehler("(HTTP 1234)")) is None
    assert r.gh_http_status(RuntimeError("(HTTP 403)")) is None


def test_warnung_nennt_statuscode(monkeypatch, capsys):
    def kaputt(*a, **k):
        raise _fehler("gh: Resource not accessible by personal access token (HTTP 403)")
    monkeypatch.setattr(r, "gh_text", kaputt)
    assert r.post_review_status(SHA, "**Freigegeben**") is False
    out = capsys.readouterr().out
    assert "⚠ Stephan: Status review für cccccc" in out
    assert "CalledProcessError, HTTP 403" in out
    assert "Resource not accessible" not in out


def test_warnung_ohne_code_wie_bisher(monkeypatch, capsys):
    def kaputt(*a, **k):
        raise _fehler("geheim-im-fehlertext")
    monkeypatch.setattr(r, "gh_text", kaputt)
    assert r.post_review_status(SHA, "**Freigegeben**") is False
    out = capsys.readouterr().out
    assert "(CalledProcessError)" in out and "HTTP" not in out
    assert "geheim-im-fehlertext" not in out
