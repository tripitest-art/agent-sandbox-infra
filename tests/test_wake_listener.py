"""Unit-Tests für sandbox/wake_listener.py (reine Logik, Fakes, kein Netzwerk)."""

from __future__ import annotations

import json
import subprocess
import urllib.error
from unittest.mock import MagicMock

import pytest

from sandbox import wake_listener as wl


class Clock:
    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class Runner:
    def __init__(self, returncode: int = 0, exc: Exception | None = None):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.exc = exc

    def __call__(self, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(list(args))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(args, self.returncode)


class FakeResponse:
    def __init__(self, lines: list[bytes | str]):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self.lines)


# ---------------- is_wake_event ----------------

def test_is_wake_event_gueltige_ereignisse():
    assert wl.is_wake_event('{"event": "message"}') is True
    assert wl.is_wake_event('{"event": "message", "extra": 123}') is True
    assert wl.is_wake_event('  {"event": "message"}  \n') is True


@pytest.mark.parametrize(
    "line",
    [
        '{"event": "open"}',
        '{"event": "keepalive"}',
        '{"event": "poll_request"}',
        '{"event": null}',
        '{"event": 42}',
        '{"no_event": "message"}',
        "",
        "   ",
        "kein json",
        "[1, 2, 3]",
        '"message"',
        123,  # type: ignore
        None,  # type: ignore
    ],
)
def test_is_wake_event_ungueltige_oder_andere_ereignisse(line):
    assert wl.is_wake_event(line) is False


# ---------------- stream_url ----------------

def test_stream_url_gueltig():
    url = "https://ntfy.sh/mein-geheimes-thema/json"
    assert wl.stream_url(url) == url
    assert wl.stream_url(f"  {url}\n") == url
    http_url = "http://localhost:8080/stream/json"
    assert wl.stream_url(http_url) == http_url


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        None,
        "nur-ein-wort",
        "ftp://example.com/stream",
        "file:///etc/passwd",
        "http://",
        "https://",
        "https:///nur/pfad",
    ],
)
def test_stream_url_ungueltig(bad):
    assert wl.stream_url(bad) is None


# ---------------- Waker ----------------

def test_waker_startet_beim_ersten_wake_event():
    runner = Runner()
    clock = Clock(100.0)
    waker = wl.Waker(["u1.service", "u2.service"], min_gap=30.0, clock=clock, runner=runner)

    assert waker.on_line('{"event": "message"}') is True
    assert runner.calls == [["systemctl", "start", "--no-block", "u1.service", "u2.service"]]


def test_waker_entprellen_und_nachlauf():
    runner = Runner()
    clock = Clock(10.0)
    waker = wl.Waker(["u1.service"], min_gap=20.0, clock=clock, runner=runner)

    # 1. Start bei t=10
    assert waker.on_line('{"event": "message"}') is True
    assert len(runner.calls) == 1

    # Weiteres Signal bei t=15 (innerhalb Sperrzeit) -> nicht sofort starten
    clock.t = 15.0
    assert waker.on_line('{"event": "message"}') is False
    assert len(runner.calls) == 1

    # Noch ein Signal bei t=20 -> weiterhin in Sperrzeit
    clock.t = 20.0
    assert waker.on_line('{"event": "message"}') is False
    assert len(runner.calls) == 1

    # Poll vor Ablauf der Sperrzeit (t=29) -> noch nicht
    clock.t = 29.0
    assert waker.poll() is False
    assert len(runner.calls) == 1

    # Poll nach Ablauf der Sperrzeit (t=30) -> startet genau einmal (Nachlauf)
    clock.t = 30.0
    assert waker.poll() is True
    assert len(runner.calls) == 2

    # Weiteres Poll ohne neue Signale -> tut nichts
    clock.t = 50.0
    assert waker.poll() is False
    assert len(runner.calls) == 2


def test_waker_runner_fehler_und_rueckgabecode(capsys):
    clock = Clock(0.0)
    # Runner gibt Exit-Code 2 zurück
    waker_fail = wl.Waker(["u1.service"], min_gap=10.0, clock=clock, runner=Runner(returncode=2))
    assert waker_fail.on_line('{"event": "message"}') is False
    assert "Rückgabecode 2" in capsys.readouterr().err

    # Runner wirft Ausnahme
    waker_exc = wl.Waker(
        ["u1.service"],
        min_gap=10.0,
        clock=clock,
        runner=Runner(exc=RuntimeError("streng_geheim")),
    )
    assert waker_exc.on_line('{"event": "message"}') is False
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "streng_geheim" not in err


# ---------------- run_once ----------------

def test_run_once_verarbeitet_zeilen():
    lines = [
        b'{"event": "open"}\n',
        b'{"event": "keepalive"}\n',
        b'{"event": "message"}\n',
    ]
    runner = Runner()
    waker = wl.Waker(["u.service"], runner=runner)

    wl.run_once("https://ntfy.example/stream", waker, opener=lambda req, timeout: FakeResponse(lines))
    assert len(runner.calls) == 1


def test_run_once_faengt_timeout_still(capsys):
    class TimeoutIter:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            raise TimeoutError("connection timed out")

    runner = Runner()
    waker = wl.Waker(["u.service"], runner=runner)
    wl.run_once("https://ntfy.example/stream", waker, opener=lambda req, timeout: TimeoutIter())
    assert capsys.readouterr().err == ""
    assert runner.calls == []


def test_run_once_faengt_url_error_mit_timeout_reason(capsys):
    def boom(req, timeout):
        raise urllib.error.URLError(TimeoutError())

    runner = Runner()
    waker = wl.Waker(["u.service"], runner=runner)
    wl.run_once("https://ntfy.example/stream", waker, opener=boom)
    assert capsys.readouterr().err == ""
    assert runner.calls == []


def test_run_once_protokolliert_andere_ausnahmen_ohne_url(capsys):
    def boom(req, timeout):
        raise ConnectionResetError("verbunden zu https://geheim.example.org/stream")

    runner = Runner()
    waker = wl.Waker(["u.service"], runner=runner)
    wl.run_once("https://geheim.example.org/stream", waker, opener=boom)
    err = capsys.readouterr().err
    assert "ConnectionResetError" in err
    assert "geheim.example.org" not in err


# ---------------- main() ----------------

def test_main_ohne_url_bricht_ab(monkeypatch, capsys):
    monkeypatch.delenv("WAKE_URL", raising=False)
    monkeypatch.setenv("WAKE_UNITS", "u1.service")
    with pytest.raises(SystemExit) as exc:
        wl.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "WAKE_URL" in err


def test_main_ungueltige_url_nennt_keinen_wert(monkeypatch, capsys):
    monkeypatch.setenv("WAKE_URL", "geheimer-themenname-ohne-schema")
    monkeypatch.setenv("WAKE_UNITS", "u1.service")
    with pytest.raises(SystemExit) as exc:
        wl.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "WAKE_URL" in err
    assert "geheimer-themenname-ohne-schema" not in err


def test_main_ohne_units_bricht_ab(monkeypatch, capsys):
    monkeypatch.setenv("WAKE_URL", "https://ntfy.example.com/topic/json")
    monkeypatch.delenv("WAKE_UNITS", raising=False)
    with pytest.raises(SystemExit) as exc:
        wl.main()
    assert exc.value.code == 1
    assert "WAKE_UNITS" in capsys.readouterr().err


def test_main_schleifenlauf(monkeypatch):
    monkeypatch.setenv("WAKE_URL", "https://ntfy.example.com/topic/json")
    monkeypatch.setenv("WAKE_UNITS", "worker.service review.service")
    monkeypatch.setenv("WAKE_MIN_GAP", "15")

    runs: list[int] = []

    def fake_run_once(url, waker):
        runs.append(len(runs) + 1)
        waker.on_line('{"event": "message"}')

    monkeypatch.setattr(wl, "run_once", fake_run_once)

    sleep_calls: list[float] = []

    def fake_sleeper(seconds: float):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise StopIteration("Schleife beenden")

    with pytest.raises(StopIteration):
        wl.main(sleeper=fake_sleeper)

    assert runs == [1, 2]
    assert sleep_calls == [5, 5]
