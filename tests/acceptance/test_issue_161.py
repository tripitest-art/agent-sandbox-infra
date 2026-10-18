import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _load(name, datei):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / datei)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load("worker_tick_issue_test", "worker_tick.py")
r = _load("review_tick_issue_test", "review_tick.py")

RATE = "GitHub-Rate-Limit erreicht"
TOKEN = "GitHub-Token ungültig oder abgelaufen"
RATE_STDERR = "GraphQL: API rate limit already exceeded for user ID 334735739.\n"


def _fehler(stderr):
    return subprocess.CalledProcessError(1, ["gh", "pr", "list", "--repo", "x/y"], stderr=stderr)


def test_grund_ist_ein_fester_text():
    for mod in (w, r):
        assert mod.GH_GRUND_RATE_LIMIT == RATE
        assert mod.gh_fehler_grund(_fehler(RATE_STDERR)) == RATE
        assert mod.gh_fehler_grund(_fehler("You have exceeded a Secondary Rate Limit")) == RATE
        assert mod.gh_fehler_grund(_fehler("HTTP 401: Bad credentials (https://api.github.com/graphql)")) == TOKEN
        assert mod.gh_fehler_grund(_fehler("dial tcp: lookup api.github.com: no such host")) == "GitHub nicht erreichbar"
        assert mod.gh_fehler_grund(_fehler("Post: net/http: TLS handshake timeout")) == "GitHub nicht erreichbar"


def test_ohne_erkannten_grund_ist_der_text_leer():
    for mod in (w, r):
        assert mod.gh_fehler_grund(_fehler("boom")) == ""
        assert mod.gh_fehler_grund(_fehler(None)) == ""
        assert mod.gh_fehler_grund(ValueError("rate limit")) == ""


def test_grund_gibt_nie_stderr_text_weiter():
    for mod in (w, r):
        assert "334735739" not in mod.gh_fehler_grund(_fehler(RATE_STDERR))


def test_worker_meldet_denselben_grund_nur_einmal(capsys):
    state = {}
    assert w.report_gh_error(state, "-", _fehler(RATE_STDERR)) is True
    out = capsys.readouterr().out
    assert "⚠ Stephan: -: Fehler: " + RATE in out
    assert "334735739" not in out
    assert w.report_gh_error(state, "-", _fehler(RATE_STDERR)) is False
    assert capsys.readouterr().out == ""
    assert w.clear_gh_error(state) is True
    assert w.clear_gh_error(state) is False
    assert w.report_gh_error(state, "-", _fehler(RATE_STDERR)) is True


def test_worker_meldet_einen_anderen_grund_erneut(capsys):
    state = {}
    assert w.report_gh_error(state, "-", _fehler(RATE_STDERR)) is True
    capsys.readouterr()
    assert w.report_gh_error(state, "-", _fehler("HTTP 401: Bad credentials")) is True
    assert TOKEN in capsys.readouterr().out


def test_worker_ohne_grund_meldet_wie_bisher_jedes_mal(capsys):
    state = {}
    for _ in range(2):
        assert w.report_gh_error(state, "issue5", _fehler("boom")) is True
        assert "⚠ Stephan: issue5: Fehler: Command" in capsys.readouterr().out
    assert state == {}


def _review_lauf(monkeypatch, fehler):
    ablauf = []
    monkeypatch.setattr(r, "REVIEW_KEY", "k")
    monkeypatch.setattr(r, "PROJECTS", [SimpleNamespace(name="a"), SimpleNamespace(name="b")])
    monkeypatch.setattr(r, "POST", False)
    monkeypatch.setattr(r, "use_project", lambda p, first: ablauf.append("use:" + p.name))

    def pr_review():
        ablauf.append("review")
        if fehler is not None:
            raise fehler

    monkeypatch.setattr(r, "_run_pr_review", pr_review)
    monkeypatch.setattr(r, "unblock_dependent_issues", lambda: ablauf.append("unblock"))
    assert r.run_once() == 0
    return ablauf


def test_review_beendet_den_lauf_beim_rate_limit(monkeypatch, capsys):
    ablauf = _review_lauf(monkeypatch, _fehler(RATE_STDERR))
    out = capsys.readouterr().out
    assert ablauf == ["use:a", "review"]
    assert "Projekt a: Fehler: CalledProcessError: " in out
    assert "(" + RATE + ")" in out
    assert "334735739" not in out


def test_review_macht_bei_anderen_fehlern_mit_dem_naechsten_projekt_weiter(monkeypatch, capsys):
    ablauf = _review_lauf(monkeypatch, _fehler("HTTP 401: Bad credentials"))
    out = capsys.readouterr().out
    assert ablauf == ["use:a", "review", "unblock", "use:b", "review", "unblock"]
    assert "Projekt a: Fehler" in out and "Projekt b: Fehler" in out
    assert "(" + TOKEN + ")" in out


def test_review_fehler_ohne_grund_bleibt_ohne_klammer(monkeypatch, capsys):
    ablauf = _review_lauf(monkeypatch, _fehler("boom"))
    out = capsys.readouterr().out
    assert ablauf == ["use:a", "review", "unblock", "use:b", "review", "unblock"]
    assert "Projekt a: Fehler: CalledProcessError: " in out
    assert RATE not in out and TOKEN not in out
