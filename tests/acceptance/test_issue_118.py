"""Akzeptanztests: Lebendprüfung von Prozessen ohne os.kill unter Windows.

Der Windows-Zweig wird mit einem Fake für ctypes.windll geprüft und läuft deshalb auch unter Linux.
"""
import ctypes
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _laden(name: str):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w = _laden("worker_tick")
q = _laden("qwen_webhook")

PRUEFUNGEN = [w._pid_alive, q.pid_is_active]
IDS = ["worker_tick._pid_alive", "qwen_webhook.pid_is_active"]


class FakeKernel32:
    def __init__(self, handle=1, exit_code=259, error=0):
        self.handle, self.exit_code, self.error = handle, exit_code, error
        self.geoeffnet, self.geschlossen = [], []

    def OpenProcess(self, access, inherit, pid):
        self.geoeffnet.append(pid)
        return self.handle

    def GetExitCodeProcess(self, handle, ref):
        ref._obj.value = self.exit_code
        return 1

    def GetLastError(self):
        return self.error

    def CloseHandle(self, handle):
        self.geschlossen.append(handle)
        return 1


@pytest.fixture
def windows(monkeypatch):
    def einrichten(**kwargs):
        k = FakeKernel32(**kwargs)

        def kein_kill(*args, **kw):
            raise AssertionError("os.kill unter Windows aufgerufen")

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=k), raising=False)
        monkeypatch.setattr(os, "kill", kein_kill)
        return k
    return einrichten


@pytest.mark.parametrize("pruefung", PRUEFUNGEN, ids=IDS)
def test_windows_laufender_prozess_lebt_und_handle_wird_geschlossen(windows, pruefung):
    k = windows(exit_code=259)
    assert pruefung(4242) is True
    assert k.geoeffnet == [4242]
    assert k.geschlossen == [1]


@pytest.mark.parametrize("pruefung", PRUEFUNGEN, ids=IDS)
def test_windows_beendeter_prozess_lebt_nicht(windows, pruefung):
    k = windows(exit_code=0)
    assert pruefung(4242) is False
    assert k.geschlossen == [1]


@pytest.mark.parametrize("pruefung", PRUEFUNGEN, ids=IDS)
def test_windows_unbekannte_pid_lebt_nicht(windows, pruefung):
    k = windows(handle=0, error=87)
    assert pruefung(4242) is False
    assert k.geschlossen == []


def test_windows_zugriff_verweigert_gilt_im_worker_als_lebend(windows):
    windows(handle=0, error=5)
    assert w._pid_alive(4242) is True


def test_windows_zugriff_verweigert_gilt_im_webhook_als_nicht_aktiv(windows):
    windows(handle=0, error=5)
    assert q.pid_is_active(4242) is False


@pytest.mark.parametrize("pruefung", PRUEFUNGEN, ids=IDS)
@pytest.mark.parametrize("pid", [None, 0, -1])
def test_ungueltige_pid_lebt_nicht_und_ruft_keine_api(windows, pruefung, pid):
    k = windows()
    assert pruefung(pid) is False
    assert k.geoeffnet == []


@pytest.mark.parametrize("pruefung", PRUEFUNGEN, ids=IDS)
def test_echte_prozesse_eigener_lebt_beendeter_nicht(pruefung):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert pruefung(os.getpid()) is True
    assert pruefung(proc.pid) is False
