import importlib.util
import os
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_pre_push", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_pre_push"] = w
_spec.loader.exec_module(w)


def test_hook_text_sperrt_bei_rot():
    h = w.pre_push_hook("python -m pytest -q")
    assert h.startswith("#!/bin/sh")
    assert "python -m pytest -q" in h
    assert "exit 1" in h
    assert "--no-verify" not in h


def test_hook_wird_geschrieben_und_ersetzt(tmp_path):
    hooks = tmp_path / "hooks"
    path = w.install_pre_push_hook(str(hooks), "echo eins")
    assert Path(path) == hooks / "pre-push"
    assert Path(path).read_text(encoding="utf-8") == w.pre_push_hook("echo eins")
    w.install_pre_push_hook(str(hooks), "echo zwei")
    assert Path(path).read_text(encoding="utf-8") == w.pre_push_hook("echo zwei")
    if os.name != "nt":
        assert Path(path).stat().st_mode & 0o111 == 0o111


def test_push_regel_erlaubt_umgehen_nur_fuer_den_draft():
    assert "--no-verify" in w.PUSH_RULE and "Draft" in w.PUSH_RULE
