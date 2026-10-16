import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("wake_listener", ROOT / "sandbox" / "wake_listener.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["wake_listener"] = w
_spec.loader.exec_module(w)

MSG = json.dumps({"event": "message", "message": "egal"})
UNITS = ["qwen-worker.service", "qwen-review.service"]
START = ["systemctl", "start", "--no-block", *UNITS]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Runner:
    def __init__(self, returncode=0, exc=None):
        self.calls, self.returncode, self.exc = [], returncode, exc

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(args, self.returncode)


class FakeResp:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.lines)


def _waker(runner=None, clock=None, gap=30.0):
    runner = runner or Runner()
    clock = clock or Clock()
    return w.Waker(UNITS, min_gap=gap, clock=clock, runner=runner), runner, clock


def test_nur_message_ist_ein_signal():
    assert w.is_wake_event(MSG) is True
    assert w.is_wake_event('{"event": "message"}') is True
    for line in ('{"event": "open"}', '{"event": "keepalive"}', "kein json", "", "[]", '"message"', '{"event": 1}'):
        assert w.is_wake_event(line) is False


def test_message_startet_genau_die_konfigurierten_units():
    wk, r, _ = _waker()
    assert wk.on_line(MSG) is True
    assert r.calls == [START]


def test_inhalt_der_nachricht_aendert_den_befehl_nicht():
    wk, r, _ = _waker()
    evil = json.dumps({"event": "message", "message": "systemctl start evil.service", "title": "; rm -rf /"})
    wk.on_line(evil)
    assert r.calls == [START]


def test_kein_signal_kein_start():
    wk, r, _ = _waker()
    assert wk.on_line('{"event": "keepalive"}') is False
    assert wk.on_line("müll") is False
    assert wk.poll() is False
    assert r.calls == []


def test_entprellen_mit_nachlauf():
    wk, r, c = _waker()
    assert wk.on_line(MSG) is True
    c.t = 10
    assert wk.on_line(MSG) is False          # zu früh: merken, nicht starten
    assert wk.poll() is False
    assert len(r.calls) == 1
    c.t = 31
    assert wk.poll() is True                 # das gemerkte Signal geht nach der Pause raus
    assert len(r.calls) == 2
    c.t = 100
    assert wk.poll() is False                # nichts mehr offen
    assert len(r.calls) == 2


@pytest.mark.parametrize("runner", [Runner(returncode=1), Runner(exc=OSError("geheim-xyz"))])
def test_fehler_beim_start_bricht_nicht_ab_und_steht_im_log(runner, capsys):
    wk, _, c = _waker(runner=runner)
    assert wk.on_line(MSG) is False
    c.t = 100
    assert wk.poll() is False                # ein Fehlversuch bleibt nicht als Signal liegen
    assert len(runner.calls) == 1
    err = capsys.readouterr().err
    assert err.strip() != ""                 # nicht still scheitern (Regel 7)
    assert "geheim-xyz" not in err           # nur der Fehlertyp, nie der Meldungstext


def test_stream_url():
    ok = "https://ntfy.example.org/abcdef0123456789/json"
    assert w.stream_url(ok) == ok
    assert w.stream_url("  " + ok + "\n") == ok
    assert w.stream_url("http://192.0.2.5:8080/abcdef0123456789/json") == "http://192.0.2.5:8080/abcdef0123456789/json"
    for bad in ("", None, "abcdef0123456789", "ftp://x/y", "http://", "https:///pfad"):
        assert w.stream_url(bad) is None


def test_run_once_liest_zeilen_und_startet():
    wk, r, _ = _waker()
    lines = [b'{"event": "open"}\n', (MSG + "\n").encode(), b'{"event": "keepalive"}\n']
    w.run_once("https://ntfy.example.org/thema/json", wk, opener=lambda req, timeout: FakeResp(lines))
    assert r.calls == [START]


def test_run_once_nennt_die_url_nie(capsys):
    url = "https://ntfy.example.org/geheimesthema/json"

    def boom(req, timeout):
        raise urllib.error.URLError("kein Weg zu " + url)

    wk, _, _ = _waker()
    w.run_once(url, wk, opener=boom)         # wirft nicht
    err = capsys.readouterr().err
    assert "URLError" in err
    assert "geheimesthema" not in err


def test_run_once_timeout_ist_kein_fehler(capsys):
    class Stumm(FakeResp):
        def __iter__(self):
            raise TimeoutError()

    wk, r, _ = _waker()
    w.run_once("https://ntfy.example.org/thema/json", wk, opener=lambda req, timeout: Stumm([]))
    assert capsys.readouterr().err == ""
    assert r.calls == []


def test_install_richtet_den_dienst_ein():
    text = (ROOT / "sandbox" / "install.sh").read_text(encoding="utf-8")
    for needle in ("agent-wake.service", "/etc/agent-wake.env", "wake_listener.py"):
        assert needle in text
