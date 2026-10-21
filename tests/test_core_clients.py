"""Tests für `sandbox/core`: gh_client (GitHub-CLI) und litellm_client (LiteLLM-Proxy).

Ohne Netzwerk und ohne `gh`: subprocess.run und urllib.request.urlopen sind durch Fakes ersetzt.
"""

import json
import subprocess
import urllib.error

import pytest

from sandbox.core import gh_client, litellm_client


class FakeRun:
    """Ersetzt subprocess.run, merkt sich die Aufrufe und liefert vorgegebene Ausgabe."""

    def __init__(self, stdout: str = "", stderr: str = "", code: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.code = code
        self.calls: list[dict] = []

    def __call__(self, cmd, capture_output=True, text=True, check=True, env=None, timeout=None):
        self.calls.append({"cmd": cmd, "env": env, "timeout": timeout})
        if self.code:
            raise subprocess.CalledProcessError(self.code, cmd, self.stdout, self.stderr)
        return subprocess.CompletedProcess(cmd, self.code, self.stdout, self.stderr)


class Antwort:
    """Minimaler Ersatz für die Antwort von urllib.request.urlopen."""

    def __init__(self, inhalt: bytes = b"{}"):
        self.inhalt = inhalt

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.inhalt


def _urlopen(monkeypatch, antwort=None, fehler: BaseException | None = None) -> list[dict]:
    """Ersetzt urlopen; merkt sich Aufrufe (url, Request, timeout)."""
    calls: list[dict] = []

    def fake(req, timeout=None):
        calls.append({"req": req, "timeout": timeout})
        if fehler is not None:
            raise fehler
        return antwort

    monkeypatch.setattr(litellm_client.urllib.request, "urlopen", fake)
    return calls


# ---- gh_client.gh ----

def test_gh_haengt_repo_an(monkeypatch):
    run = FakeRun('{"key": "value"}')
    monkeypatch.setattr(gh_client.subprocess, "run", run)
    assert gh_client.gh("issue", "list", repo="tripitest-art/test") == {"key": "value"}
    assert run.calls[0]["cmd"] == ["gh", "issue", "list", "--repo", "tripitest-art/test"]
    assert run.calls[0]["timeout"] == gh_client.GH_TIMEOUT


def test_gh_ohne_repo_haengt_nichts_an(monkeypatch):
    run = FakeRun("[]")
    monkeypatch.setattr(gh_client.subprocess, "run", run)
    assert gh_client.gh("pr", "list") == []
    assert run.calls[0]["cmd"] == ["gh", "pr", "list"]


def test_gh_api_bekommt_kein_repo(monkeypatch):
    """`gh api` kennt `--repo` nicht; das Repository steht schon im Pfad."""
    run = FakeRun('{"ok": true}')
    monkeypatch.setattr(gh_client.subprocess, "run", run)
    gh_client.gh("api", "repos/o/r/contents/x", repo="o/r")
    assert run.calls[0]["cmd"] == ["gh", "api", "repos/o/r/contents/x"]


def test_gh_leere_ausgabe_gibt_none(monkeypatch):
    monkeypatch.setattr(gh_client.subprocess, "run", FakeRun("  \n"))
    assert gh_client.gh("issue", "close", "1") is None


def test_gh_token_fehler_gibt_none(monkeypatch):
    monkeypatch.setattr(
        gh_client.subprocess, "run",
        FakeRun(code=4, stderr="gh: GH_TOKEN environment variable not set"))
    assert gh_client.gh("issue", "list") is None


def test_gh_token_fehler_ohne_token_string_in_stderr_gibt_none(monkeypatch):
    """Exitcode 4 bedeutet Token-/Auth-Fehler auch ohne 'GH_TOKEN' in stderr (Issue #219)."""
    monkeypatch.setattr(
        gh_client.subprocess, "run",
        FakeRun(code=4, stderr="authentication failed: bad credentials"))
    assert gh_client.gh("issue", "list") is None


def test_gh_andere_fehler_werden_weitergereicht(monkeypatch):
    """Rate-Limit und Netzfehler dürfen nicht als „kein Ergebnis“ durchgehen."""
    monkeypatch.setattr(
        gh_client.subprocess, "run",
        FakeRun(code=1, stderr="gh: API rate limit exceeded"))
    with pytest.raises(subprocess.CalledProcessError):
        gh_client.gh("issue", "list")


def test_gh_reicht_env_und_timeout_durch(monkeypatch):
    run = FakeRun("null")
    monkeypatch.setattr(gh_client.subprocess, "run", run)
    gh_client.gh("issue", "list", env={"GH_TOKEN": "x"}, timeout=2.5)
    assert run.calls[0]["env"] == {"GH_TOKEN": "x"}
    assert run.calls[0]["timeout"] == 2.5


# ---- gh_client.gh_json ----

def test_gh_json_parst_objekt_und_liste(monkeypatch):
    monkeypatch.setattr(gh_client.subprocess, "run", FakeRun('{"a": 1}'))
    assert gh_client.gh_json("pr", "view", "1") == {"a": 1}
    monkeypatch.setattr(gh_client.subprocess, "run", FakeRun('[{"a": 1}]'))
    assert gh_client.gh_json("pr", "list") == [{"a": 1}]


def test_gh_json_lehnt_text_ab(monkeypatch):
    monkeypatch.setattr(gh_client.subprocess, "run", FakeRun("https://github.com/o/r/issues/1"))
    with pytest.raises(ValueError):
        gh_client.gh_json("issue", "edit", "1")


def test_gh_json_leere_ausgabe_gibt_none(monkeypatch):
    monkeypatch.setattr(gh_client.subprocess, "run", FakeRun(""))
    assert gh_client.gh_json("issue", "list") is None


# ---- litellm_client.probe_model ----

def _antwort_mit_choices(monkeypatch):
    inhalt = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
    return _urlopen(monkeypatch, Antwort(inhalt))


def test_probe_model_sendet_bearer_und_modell(monkeypatch):
    calls = _antwort_mit_choices(monkeypatch)
    assert litellm_client.probe_model("http://127.0.0.1:4000", "coder", api_key="secret") == (True, None)
    req = calls[0]["req"]
    assert req.full_url == "http://127.0.0.1:4000/v1/chat/completions"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer secret"
    assert json.loads(req.data)["model"] == "coder"
    assert calls[0]["timeout"] == 5.0


def test_probe_model_entfernt_schraegstrich_am_ende(monkeypatch):
    calls = _antwort_mit_choices(monkeypatch)
    litellm_client.probe_model("http://127.0.0.1:4000/", "coder")
    assert calls[0]["req"].full_url == "http://127.0.0.1:4000/v1/chat/completions"


def test_probe_model_http_fehler_nennt_den_code(monkeypatch):
    _urlopen(monkeypatch, fehler=urllib.error.HTTPError("http://x", 429, "zu viel", None, None))
    assert litellm_client.probe_model("http://127.0.0.1:4000", "coder") == (False, "HTTP 429")


def test_probe_model_ohne_choices_ist_leer(monkeypatch):
    _urlopen(monkeypatch, Antwort(b"{}"))
    assert litellm_client.probe_model("http://127.0.0.1:4000", "coder") == (False, "leere Antwort")


def test_probe_model_kaputtes_json_nennt_nur_den_typ(monkeypatch):
    _urlopen(monkeypatch, Antwort(b"kein json"))
    ok, grund = litellm_client.probe_model("http://127.0.0.1:4000", "coder")
    assert ok is False
    assert grund == "JSONDecodeError"


def test_probe_model_nennt_den_key_nie_im_fehler(monkeypatch):
    _urlopen(monkeypatch, fehler=OSError("Connection refused"))
    ok, grund = litellm_client.probe_model("http://127.0.0.1:4000", "coder", api_key="geheim-123")
    assert ok is False and "geheim-123" not in (grund or "")


# ---- litellm_client.get_key_info ----

def test_get_key_info_liefert_das_antwort_objekt(monkeypatch):
    calls = _urlopen(monkeypatch, Antwort(b'{"info": {"spend": 1.5, "max_budget": 10}}'))
    assert litellm_client.get_key_info("http://127.0.0.1:4000", "secret") == {
        "info": {"spend": 1.5, "max_budget": 10}}
    req = calls[0]["req"]
    assert req.full_url == "http://127.0.0.1:4000/key/info"
    assert req.get_header("Authorization") == "Bearer secret"
    assert calls[0]["timeout"] == 10.0


def test_get_key_info_none_bei_http_fehler(monkeypatch):
    _urlopen(monkeypatch, fehler=urllib.error.HTTPError("http://x", 401, "nein", None, None))
    assert litellm_client.get_key_info("http://127.0.0.1:4000", "secret") is None


def test_get_key_info_none_bei_netzfehler(monkeypatch):
    _urlopen(monkeypatch, fehler=OSError("Connection refused"))
    assert litellm_client.get_key_info("http://127.0.0.1:4000", "secret") is None


def test_get_key_info_none_wenn_antwort_kein_objekt_ist(monkeypatch):
    _urlopen(monkeypatch, Antwort(b'[1, 2]'))
    assert litellm_client.get_key_info("http://127.0.0.1:4000", "secret") is None


def test_get_key_info_none_wenn_antwort_kein_json_ist(monkeypatch):
    _urlopen(monkeypatch, Antwort(b"kein json"))
    assert litellm_client.get_key_info("http://127.0.0.1:4000", "secret") is None
