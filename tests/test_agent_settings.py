"""Tests für sandbox/agent_settings.py: Rechte, atomares Schreiben und Quellenreihenfolge."""

import os
import stat
import sys
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
sys.path.insert(0, str(_SANDBOX))
import agent_settings  # noqa: E402


@pytest.fixture
def datei(tmp_path, monkeypatch):
    """Eine Einstellungsdatei in tmp_path; AGENT_SETTINGS zeigt darauf, Umgebungswerte sind leer."""
    p = tmp_path / "agent-settings.json"
    monkeypatch.setenv("AGENT_SETTINGS", str(p))
    for var in ("REVIEW_MAX_TOKENS", "REVIEW_LLM_TIMEOUT", "REVIEW_MODEL_BIG"):
        monkeypatch.delenv(var, raising=False)
    return p


def test_save_schreibt_mit_modus_644(datei):
    if os.name == "nt":
        return   # chmod hat unter Windows keine Bedeutung
    agent_settings.save({"MAX_ROUNDS": 3})
    assert stat.S_IMODE(datei.stat().st_mode) == 0o644


def test_value_nimmt_review_llm_timeout_aus_der_umgebung(datei, monkeypatch):
    monkeypatch.setenv("REVIEW_LLM_TIMEOUT", "600")
    assert agent_settings.value("REVIEW_LLM_TIMEOUT", 420) == 600
    monkeypatch.setenv("REVIEW_LLM_TIMEOUT", "901")   # außerhalb 60..900
    assert agent_settings.value("REVIEW_LLM_TIMEOUT", 420) == 420
    monkeypatch.setenv("REVIEW_LLM_TIMEOUT", "viel")
    assert agent_settings.value("REVIEW_LLM_TIMEOUT", 420) == 420


def test_save_bei_nicht_schreibbarem_ordner_wirft_oserror(tmp_path, monkeypatch):
    # Als root (Server) hält chmod niemanden auf. Ein Pfad, dessen Elternordner eine reguläre
    # Datei ist, scheitert dagegen für jeden Benutzer mit OSError.
    sperre = tmp_path / "kein-ordner"
    sperre.write_text("bleibt", encoding="utf-8")
    monkeypatch.setenv("AGENT_SETTINGS", str(sperre / "agent-settings.json"))
    with pytest.raises(OSError):
        agent_settings.save({"MAX_ROUNDS": 3})
    assert sperre.read_text(encoding="utf-8") == "bleibt"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kein-ordner"]


def test_save_laesst_die_datei_bei_schreibfehler_unveraendert(datei, monkeypatch):
    agent_settings.save({"MAX_ROUNDS": 3})
    vorher = datei.read_text(encoding="utf-8")

    def kein_schreiben(*args, **kwargs):
        raise PermissionError("Ordner nicht schreibbar")

    monkeypatch.setattr(agent_settings.tempfile, "mkstemp", kein_schreiben)
    with pytest.raises(OSError):
        agent_settings.save({"REVIEW_MAX_TOKENS": 32000})
    assert datei.read_text(encoding="utf-8") == vorher
    assert [p.name for p in datei.parent.iterdir()] == [datei.name]
