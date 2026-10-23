import importlib.util
import sys
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def agent_conf(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_SANDBOX_CONF", str(tmp_path / "agent-sandbox.conf"))
    return _load("agent_conf")


def test_ohne_datei_ergibt_beide_werte_none(agent_conf):
    assert agent_conf.lese_agent_konfiguration() == {"agent": None, "context_mode": None}


def test_liest_agent_und_context_mode(agent_conf, tmp_path):
    (tmp_path / "agent-sandbox.conf").write_text("AGENT=opencode\nCONTEXT_MODE=1\n", encoding="utf-8")
    assert agent_conf.lese_agent_konfiguration() == {"agent": "opencode", "context_mode": True}


def test_context_mode_0_ist_false_nicht_none(agent_conf, tmp_path):
    (tmp_path / "agent-sandbox.conf").write_text("AGENT=qwen-code\nCONTEXT_MODE=0\n", encoding="utf-8")
    assert agent_conf.lese_agent_konfiguration() == {"agent": "qwen-code", "context_mode": False}


def test_unbekannte_werte_ergeben_none(agent_conf, tmp_path):
    (tmp_path / "agent-sandbox.conf").write_text("AGENT=sonstwas\nCONTEXT_MODE=ja\n", encoding="utf-8")
    assert agent_conf.lese_agent_konfiguration() == {"agent": None, "context_mode": None}


def test_dashboard_state_enthaelt_agent_konfiguration(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_SANDBOX_CONF", str(tmp_path / "agent-sandbox.conf"))
    monkeypatch.setenv("AGENT_SETTINGS", str(tmp_path / "agent-settings.json"))
    (tmp_path / "agent-sandbox.conf").write_text("AGENT=opencode\nCONTEXT_MODE=1\n", encoding="utf-8")
    dash_mod = _load("dashboard")
    log = tmp_path / "worker.log"
    log.touch()
    dash = dash_mod.Dashboard(
        dash_mod.StatusCache(lambda: {}, ttl=60), dash_mod.LogBuffer(str(log)),
        slot_dir=str(tmp_path / "slots"), infra_dir=str(tmp_path),
        is_pid_alive=lambda pid: True,
    )
    assert dash.state()["agent_konfiguration"] == {"agent": "opencode", "context_mode": True}
