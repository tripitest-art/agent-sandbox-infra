# tests/acceptance/test_issue_403.py
import os
from pathlib import Path
import pytest
import sandbox.dashboard as d
import sandbox.worker_tick as w

def test_pipeline_pause_acceptance(tmp_path, monkeypatch):
    pause_datei = tmp_path / "pipeline-pause"
    monkeypatch.setattr(w, "PAUSE_FILE", str(pause_datei))
    
    # Standardmaessig nicht pausiert
    assert not w.pipeline_pausiert()
    
    # Pausieren aktivieren
    pause_datei.write_text("pausiert", encoding="utf-8")
    assert w.pipeline_pausiert()
    
    # find_job blockiert neue Issues bei aktiver Pause
    monkeypatch.setattr(w, "issue_job", lambda *a, **k: ("issue100", "task", None, None))
    monkeypatch.setattr(w, "rework_job", lambda *a: None)
    monkeypatch.setattr(w, "text_fix_job", lambda *a: None)
    monkeypatch.setattr(w, "main_is_red", lambda *a: False)
    monkeypatch.setattr(w, "main_is_red_local", lambda *a: False)
    monkeypatch.setattr(w, "deploy_steht_an", lambda *a: False)
    assert w.find_job({}) is None
    
    # Wieder freigeben
    pause_datei.unlink()
    assert not w.pipeline_pausiert()
    assert w.find_job({}) == ("issue100", "task", None, None)
