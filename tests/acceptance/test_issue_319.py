import importlib.util
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_push_haertung", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_push_haertung"] = w
_spec.loader.exec_module(w)


def test_open_pr_pusht_ueber_den_root_eigenen_hooks_pfad(monkeypatch, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    claims = tmp_path / "claims"
    monkeypatch.setattr(w, "WORKDIR", str(wt))
    monkeypatch.setattr(w, "CLAIM_DIR", str(claims))
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", "## Vorlage\n")
    calls = []

    def fake_gh(*args):
        if args[:2] == ("pr", "list"):
            return []
        if args[:2] == ("issue", "view"):
            return {"title": "Titel des Issues"}
        if args[:2] == ("pr", "create"):
            return "https://example.invalid/pull/99"
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    def fake_run(cmd, **kw):
        calls.append((list(cmd), kw))
        out = ""
        if list(cmd[:3]) == ["git", "rev-list", "--count"]:
            out = "3\n"
        elif list(cmd[:2]) == ["git", "log"]:
            out = "Akzeptanztests aus #7\x00Baut X\x00"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(w, "gh", fake_gh)
    monkeypatch.setattr(w.subprocess, "run", fake_run)

    assert w.open_pr(7) is True
    pushes = [(c, kw) for c, kw in calls if list(c[:2]) == ["git", "push"]]
    (push_cmd, push_kw), = pushes
    assert push_cmd == ["git", "push", "-u", "origin", "feature/issue-7"]   # Befehl unverändert (#172)
    env = push_kw["env"]
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert env["GIT_CONFIG_VALUE_0"] == str(claims / "pre-push-hooks")
    assert env["GIT_CONFIG_KEY_1"] == "core.fsmonitor"
    assert env["GIT_CONFIG_VALUE_1"] == "false"


def test_ensure_pre_push_hook_schreibt_in_root_eigenes_verzeichnis(monkeypatch, tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    claims = tmp_path / "claims"
    monkeypatch.setattr(w, "WORKDIR", str(wd))
    monkeypatch.setattr(w, "CLAIM_DIR", str(claims))
    path = w.ensure_pre_push_hook()
    hook = claims / "pre-push-hooks" / "pre-push"
    assert path == str(hook)
    assert hook.read_text(encoding="utf-8") == w.pre_push_hook(w.TEST_CMD)
    assert wd not in hook.parents          # außerhalb des Worktrees: der Agent kann ihn nicht ersetzen
