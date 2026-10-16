"""Akzeptanztests für Issue #<N>: Die Testsuite hängt nicht von der Umgebung des Servers ab.

Auf dem Server (CT 112) sind BACKEND/WORKER_SLOT exportiert und /etc/agent-projects.json nennt zwei
Projekte; die Suite war dort rot, in CI grün. Die Tests laufen hier in einem Unterprozess mit genau
dieser Umgebung und müssen trotzdem grün sein.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITES = ["tests/test_worker_tick.py", "tests/test_review_tick.py", "tests/test_projects.py"]
SERVER_VARS = ("BACKEND", "WORKER_SLOT", "WAKE_PC", "AGENT_PROJECTS", "REVIEW_POST",
               "REVIEW_AUTO_MERGE", "REVIEW_MODEL", "REVIEW_MODEL_BIG", "REVIEWER_NAME", "MAX_WORKERS")


def _run(env_overrides: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in SERVER_VARS}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *SUITES],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)


def test_suiten_gruen_in_sauberer_umgebung():
    result = _run({"AGENT_PROJECTS": str(ROOT / "nicht-vorhanden.json")})
    assert result.returncode == 0, result.stdout[-3000:]


def test_suiten_gruen_in_der_umgebung_des_servers(tmp_path):
    projects = tmp_path / "agent-projects.json"
    projects.write_text(json.dumps({"projects": [
        {"name": "sc-digger", "repo": "tripitest-art/sc-digger", "workdir": "/root/sc-digger"},
        {"name": "agent-sandbox-infra", "repo": "tripitest-art/agent-sandbox-infra",
         "workdir": "/root/work/agent-sandbox-infra", "auto_merge": False}]}), encoding="utf-8")
    result = _run({"BACKEND": "litellm", "WORKER_SLOT": "1", "WAKE_PC": "1", "MAX_WORKERS": "2",
                   "AGENT_PROJECTS": str(projects), "REVIEW_POST": "1", "REVIEW_AUTO_MERGE": "1",
                   "REVIEW_MODEL": "coder-glm", "REVIEW_MODEL_BIG": "longctx-lite", "REVIEWER_NAME": "GLM"})
    assert result.returncode == 0, result.stdout[-3000:]
