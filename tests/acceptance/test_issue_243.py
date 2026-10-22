import importlib.util
import json
import sys
from pathlib import Path
import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

_PATH = _SANDBOX / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_infra", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_infra"] = w
_spec.loader.exec_module(w)


def test_sandbox_health_check_erkennt_fehlendes_verzeichnis(tmp_path):
    fehlt = tmp_path / "nicht_da"
    assert w.sandbox_health_check(fehlt) is False


def test_sandbox_health_check_erfolgreich_bei_schreibbarem_ordner(tmp_path):
    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    assert w.sandbox_health_check(ok_dir) is True


def test_detect_infra_error_erkennt_eacces_und_rechtefehler():
    stack = (
        "An unexpected critical error occurred:\n"
        "Error: EACCES: permission denied, lstat '/root/work'\n"
        "    at realpathSync (node:fs:2791:29)\n"
    )
    res = w.detect_infra_error(stack)
    assert res is not None
    assert "EACCES" in res or "permission denied" in res


def test_detect_infra_error_ignoriert_normale_testfehler():
    out = "FAILED tests/test_foo.py::test_bar - AssertionError: assert False\n"
    assert w.detect_infra_error(out) is None


def test_release_failed_claim_setzt_label_auf_bereit(monkeypatch):
    aufrufe = []
    monkeypatch.setattr(w, "gh", lambda *a: aufrufe.append(a))
    ok = w.release_failed_claim(234, "EACCES: permission denied")
    assert ok is True
    assert ("issue", "edit", "234", "--add-label", "bereit", "--remove-label", "in-arbeit") in aufrufe


def test_run_agent_uebergibt_group_wenn_user_gesetzt(monkeypatch, tmp_path):
    popen_kwargs = {}

    def fake_popen(cmd, **kwargs):
        popen_kwargs.update(kwargs)
        class DummyProc:
            stdout = iter([])
            returncode = 0
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return DummyProc()

    monkeypatch.setattr(w.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(w, "agent_identitaet", lambda: ("worker", str(tmp_path)))
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(w, "RUN_TIMEOUT", 10)
    w.run_agent("test")
    assert popen_kwargs.get("user") == "worker"
    assert popen_kwargs.get("group") == "worker"


def test_main_infrastrukturfehler_verbraucht_keinen_versuch_und_gibt_claim_frei(tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"issue42": 0}), encoding="utf-8")
    monkeypatch.setattr(w, "STATE", str(state_file))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "PREFIX", "")
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "pick_job", lambda state: ("issue42", "Auftrag", None, None))
    monkeypatch.setattr(w, "choose_model", lambda: ("coder", []))
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "slot_status", lambda *a, **k: None)
    monkeypatch.setattr(w, "check_issue_result", lambda k: None)
    monkeypatch.setattr(w, "sandbox_health_check", lambda *a, **k: True)

    freigegeben = []
    monkeypatch.setattr(w, "release_failed_claim", lambda n, g: freigegeben.append((n, g)) or True)

    def crash_run(prompt):
        w.log_stream_line("Error: EACCES: permission denied, lstat '/root/work'")
        return 1

    monkeypatch.setattr(w, "run_agent", crash_run)
    assert w.main() == 0

    neu_state = json.loads(state_file.read_text(encoding="utf-8"))
    assert neu_state.get("issue42", 0) == 0
    assert (42, "EACCES: permission denied, lstat '/root/work'") in freigegeben or any(x[0] == 42 for x in freigegeben)
