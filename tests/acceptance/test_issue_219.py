import subprocess
import sys
from pathlib import Path
import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

from sandbox.core import gh_client, litellm_client


def test_gh_client_parst_json_erfolgreich(monkeypatch):
    def fake_run(cmd, capture_output=True, text=True, check=True, env=None, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, '{"key": "value"}', "")

    monkeypatch.setattr(gh_client.subprocess, "run", fake_run)
    res = gh_client.gh("issue", "list", repo="tripitest-art/test")
    assert res == {"key": "value"}


def test_gh_client_faellt_auf_text_zurueck_wenn_kein_json(monkeypatch):
    def fake_run(cmd, capture_output=True, text=True, check=True, env=None, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, "https://github.com/foo/bar/issues/1\n", "")

    monkeypatch.setattr(gh_client.subprocess, "run", fake_run)
    res = gh_client.gh("issue", "edit", "1", "--add-label", "in-arbeit")
    assert res == "https://github.com/foo/bar/issues/1"


def test_probe_model_erfolgreich_bei_http_200(monkeypatch):
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"choices": [{"message": {"content": "pong"}}]}'

    monkeypatch.setattr(litellm_client.urllib.request, "urlopen", lambda req, timeout: FakeResponse())
    ok, err = litellm_client.probe_model("http://127.0.0.1:4000", "coder", api_key="secret")
    assert ok is True
    assert err is None


def test_probe_model_faengt_netzwerkfehler_ab(monkeypatch):
    def fake_urlopen(req, timeout):
        raise OSError("Connection refused")

    monkeypatch.setattr(litellm_client.urllib.request, "urlopen", fake_urlopen)
    ok, err = litellm_client.probe_model("http://127.0.0.1:4000", "coder")
    assert ok is False
    assert err is not None
