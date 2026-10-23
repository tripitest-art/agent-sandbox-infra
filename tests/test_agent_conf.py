import importlib.util
import os
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _konfiguration(tmp_path, monkeypatch, inhalt):
    pfad = tmp_path / "agent-sandbox.conf"
    pfad.write_text(inhalt, encoding="utf-8")
    monkeypatch.setenv("AGENT_SANDBOX_CONF", str(pfad))
    return _load("agent_conf")


def test_fehlende_datei_ergibt_none(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SANDBOX_CONF", str(tmp_path / "gibt-es-nicht.conf"))
    assert _load("agent_conf").lese_agent_konfiguration() == {
        "agent": None, "context_mode": None,
    }


def test_unlesbare_datei_ergibt_none(tmp_path, monkeypatch):
    # Ein Verzeichnis statt einer Datei löst IsADirectoryError (OSError) aus.
    monkeypatch.setenv("AGENT_SANDBOX_CONF", str(tmp_path))
    assert _load("agent_conf").lese_agent_konfiguration() == {
        "agent": None, "context_mode": None,
    }


def test_fehlende_rechte_ergeben_none(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root ignoriert Dateirechte; der Fall ist dann nicht prüfbar")
    conf = _konfiguration(tmp_path, monkeypatch, "AGENT=opencode\n")
    pfad = tmp_path / "agent-sandbox.conf"
    pfad.chmod(0o000)
    try:
        assert conf.lese_agent_konfiguration() == {"agent": None, "context_mode": None}
    finally:
        pfad.chmod(0o600)


def test_fremde_schluessel_werden_ignoriert(tmp_path, monkeypatch):
    conf = _konfiguration(
        tmp_path, monkeypatch,
        "MAX_WORKERS=4\n# Kommentar\nREVIEW_POST=1\nAGENT=qwen-code\n",
    )
    assert conf.lese_agent_konfiguration() == {"agent": "qwen-code", "context_mode": None}


def test_leere_und_kaputte_zeilen_ergeben_none(tmp_path, monkeypatch):
    conf = _konfiguration(tmp_path, monkeypatch, "\nAGENT=\nCONTEXT_MODE=\nohne-gleich\n")
    assert conf.lese_agent_konfiguration() == {"agent": None, "context_mode": None}


def test_letzte_zuweisung_gewinnt(tmp_path, monkeypatch):
    conf = _konfiguration(tmp_path, monkeypatch, "AGENT=qwen-code\nAGENT=opencode\n")
    assert conf.lese_agent_konfiguration() == {"agent": "opencode", "context_mode": None}


def test_kontext_mode_1_ist_true(tmp_path, monkeypatch):
    conf = _konfiguration(tmp_path, monkeypatch, "AGENT=opencode\nCONTEXT_MODE=1\n")
    assert conf.lese_agent_konfiguration() == {"agent": "opencode", "context_mode": True}


def test_vorgabe_pfad_ohne_umgebungsvariable(monkeypatch):
    monkeypatch.delenv("AGENT_SANDBOX_CONF", raising=False)
    assert _load("agent_conf").VORGABE_PFAD == "/etc/agent-sandbox.conf"
