import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_autodeploy_module_exists():
    mod = _load("autodeploy")
    assert hasattr(mod, "deploy_once")
    assert hasattr(mod, "is_worker_or_review_active")
    assert hasattr(mod, "DeployResult")


def test_deploy_skipped_when_busy(tmp_path):
    mod = _load("autodeploy")
    commands = []
    notifications = []

    def fake_run(cmd):
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    res = mod.deploy_once(
        tmp_path,
        fake_run,
        notifications.append,
        is_busy=lambda: True,
    )
    assert res.status == "uebersprungen"
    assert "aktiv" in res.detail
    assert len(commands) == 0


def test_deploy_skipped_when_dirty(tmp_path):
    mod = _load("autodeploy")
    notifications = []

    def fake_run(cmd):
        c = list(cmd)
        if c[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, " M sandbox/install.sh\n", "")
        if c[:3] == ["git", "fetch", "--quiet"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if c[:3] == ["git", "rev-parse", "--short"]:
            return subprocess.CompletedProcess(cmd, 0, "deadbeef\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    res = mod.deploy_once(
        tmp_path,
        fake_run,
        notifications.append,
        is_busy=lambda: False,
    )
    assert res.status == "uebersprungen"
    assert "lokale Änderungen" in res.detail
    assert any("Lokale Änderungen" in n for n in notifications)


def test_deploy_unveraendert(tmp_path):
    mod = _load("autodeploy")
    notifications = []

    def fake_run(cmd):
        c = list(cmd)
        if c[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if c[:3] == ["git", "fetch", "--quiet"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if c[:3] == ["git", "rev-parse", "--short"]:
            return subprocess.CompletedProcess(cmd, 0, "c0ffee\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    state_file = tmp_path / ".deploy-state.json"
    state_file.write_text(json.dumps({"last_good": "c0ffee"}), encoding="utf-8")

    res = mod.deploy_once(
        tmp_path,
        fake_run,
        notifications.append,
        is_busy=lambda: False,
    )
    assert res.status == "unveraendert"
    assert res.old == "c0ffee"
    assert res.new == "c0ffee"


def test_deploy_success(tmp_path):
    mod = _load("autodeploy")
    commands = []
    notifications = []

    def fake_run(cmd):
        c = list(cmd)
        commands.append(c)
        if c[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if c[:3] == ["git", "fetch", "--quiet"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if c[:3] == ["git", "rev-parse", "--short"]:
            if "HEAD" in c:
                return subprocess.CompletedProcess(cmd, 0, "1111111\n", "")
            return subprocess.CompletedProcess(cmd, 0, "2222222\n", "")
        if c[:3] == ["git", "pull", "--ff-only"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    state_file = tmp_path / ".deploy-state.json"
    state_file.write_text(json.dumps({"last_good": "1111111"}), encoding="utf-8")

    res = mod.deploy_once(
        tmp_path,
        fake_run,
        notifications.append,
        is_busy=lambda: False,
        test_cmd=["test-runner"],
        install_cmd=["installer"],
    )
    assert res.status == "ausgerollt"
    assert res.old == "1111111"
    assert res.new == "2222222"
    assert any("ausgerollt" in n for n in notifications)

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["last_good"] == "2222222"
    assert state.get("bad") is None


def test_deploy_tests_failed_rollback(tmp_path):
    mod = _load("autodeploy")
    commands = []
    notifications = []

    def fake_run(cmd):
        c = list(cmd)
        commands.append(c)
        if c[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if c[:3] == ["git", "fetch", "--quiet"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if c[:3] == ["git", "rev-parse", "--short"]:
            if "HEAD" in c:
                return subprocess.CompletedProcess(cmd, 0, "1111111\n", "")
            return subprocess.CompletedProcess(cmd, 0, "3333333\n", "")
        if c == ["test-runner"]:
            return subprocess.CompletedProcess(cmd, 1, "", "pytest error")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    state_file = tmp_path / ".deploy-state.json"
    state_file.write_text(json.dumps({"last_good": "1111111"}), encoding="utf-8")

    res = mod.deploy_once(
        tmp_path,
        fake_run,
        notifications.append,
        is_busy=lambda: False,
        test_cmd=["test-runner"],
        install_cmd=["installer"],
    )
    assert res.status == "zurueckgerollt"
    assert res.old == "1111111"
    assert res.new == "3333333"
    assert any(c[:3] == ["git", "reset", "--hard"] for c in commands)
    assert any("⚠ Stephan:" in n for n in notifications)

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["last_good"] == "1111111"
    assert state["bad"] == "3333333"


def test_systemd_units_and_install_sh():
    infra = _SANDBOX.parent
    svc = infra / "sandbox" / "systemd" / "agent-sandbox-deploy.service"
    timer = infra / "sandbox" / "systemd" / "agent-sandbox-deploy.timer"
    install_sh = infra / "sandbox" / "install.sh"

    assert svc.exists()
    assert timer.exists()

    svc_text = svc.read_text(encoding="utf-8")
    assert "agent-sandbox-deploy" in svc_text or "autodeploy" in svc_text
    assert "/root/worker.log" in svc_text

    timer_text = timer.read_text(encoding="utf-8")
    assert "15min" in timer_text

    install_text = install_sh.read_text(encoding="utf-8")
    assert "AUTODEPLOY" in install_text
