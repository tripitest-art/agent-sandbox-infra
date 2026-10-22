import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SANDBOX = ROOT / "sandbox"

if not (shutil.which("bash") and shutil.which("python3")):
    if os.environ.get("CI"):
        raise RuntimeError("bash und python3 fehlen in CI")


def _sh(script: str, tmp_path: Path, **env) -> subprocess.CompletedProcess:
    full = {
        **os.environ,
        "AGENT_SANDBOX_CONF": str(tmp_path / "agent-sandbox.conf"),
        "SCRIPT_DIR": str(SANDBOX),
        "INFRA_DIR": str(tmp_path / "deploy"),
        **env,
    }
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n. {SANDBOX}/install-lib.sh\n{script}"],
        capture_output=True,
        text=True,
        env=full,
    )


def test_agent_check_akzeptiert_qwen_code_und_opencode(tmp_path):
    assert _sh("AGENT=qwen-code CONTEXT_MODE=0; agent_check", tmp_path).returncode == 0
    assert _sh("AGENT=opencode CONTEXT_MODE=1; agent_check", tmp_path).returncode == 0
    bad = _sh("AGENT=unbekannt; agent_check", tmp_path)
    assert bad.returncode != 0 and "AGENT" in bad.stderr
    bad_cm = _sh("AGENT=opencode CONTEXT_MODE=ja; agent_check", tmp_path)
    assert bad_cm.returncode != 0 and "CONTEXT_MODE" in bad_cm.stderr


def test_opencode_config_json_enthaelt_haertung_und_sampling(tmp_path):
    r = _sh(
        "BACKEND=litellm OLLAMA=http://192.0.2.1:11434 MODEL=qwen3-coder-64k "
        "LITELLM=http://192.0.2.2:4000 LITELLM_MODEL=coder LITELLM_ALL='coder coder-free' "
        "CONTEXT_MODE=0; opencode_config_json",
        tmp_path,
    )
    assert r.returncode == 0, r.stderr
    cfg = json.loads(r.stdout)
    assert cfg["model"] == "litellm/coder"
    perm = cfg["permission"]
    for allowed in ("edit", "bash"):
        assert perm[allowed] == "allow"
    for denied in ("webfetch", "question", "plan_enter", "plan_exit", "external_directory"):
        assert perm[denied] == "deny"
    assert "plugin" not in cfg
    ollama_model = cfg["provider"]["ollama"]["models"]["qwen3-coder-64k"]
    assert ollama_model["options"]["temperature"] == 0.7


def test_opencode_config_json_aktiviert_context_mode_plugin(tmp_path):
    r = _sh(
        "BACKEND=litellm OLLAMA=http://192.0.2.1:11434 MODEL=qwen3-coder-64k "
        "LITELLM=http://192.0.2.2:4000 LITELLM_MODEL=coder LITELLM_ALL='coder' "
        "CONTEXT_MODE=1; opencode_config_json",
        tmp_path,
    )
    assert r.returncode == 0, r.stderr
    cfg = json.loads(r.stdout)
    assert cfg["plugin"] == ["context-mode"]


def test_install_sh_merkt_agent_und_context_mode():
    install_text = (SANDBOX / "install.sh").read_text(encoding="utf-8")
    assert "conf_get AGENT" in install_text
    assert "conf_get CONTEXT_MODE" in install_text
    assert "agent_check" in install_text
    assert "opencode_config_json" in install_text
