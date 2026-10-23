"""Guthaben-Hinweis beim Ausweichen auf ein bezahltes Modell (#293).

Prüft die Zeile in `main()`, nicht nur die reine Funktion: mit gefakter Modellprobe und
gefaktem Rest des Laufs (GitHub, Agent, Arbeitsbaum) muss nach „weiche auf … aus.“ genau
die Hinweiszeile folgen – und nur dann.
"""

import importlib.util
import sys
import types
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_ausweich", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_ausweich"] = w
_spec.loader.exec_module(w)

ISSUE_BODY = ("### Ziel\n\nAbstand anzeigen.\n\n"
              "### Betroffene Dateien\n\n- `sc_digger/rekordbox.py`\n\n"
              "### Akzeptanztests\n\n```python\ndef test_x():\n    pass\n```")


class FakeGH:
    def __init__(self, issue_no=102):
        self.issue = {"number": issue_no, "title": "Testauftrag", "body": ISSUE_BODY,
                      "author": {"login": "tripitest-art"}}
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return []
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else [self.issue]
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": self.issue["title"]}
        if args[:2] == ("issue", "edit"):
            return None
        if args[:2] == ("run", "list"):
            return []
        if args and args[0] == "api":
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


@pytest.fixture
def lauf(monkeypatch, tmp_path):
    """LiteLLM-Lauf ohne Netz, GitHub und Arbeitsbaum; nur `probe_model` bleibt beim Test."""
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "WORKER_SLOT", "")
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path / "workdir"))
    p = w.projects.default_projects()[0]
    monkeypatch.setattr(w, "PROJECTS", [p])
    monkeypatch.setattr(w, "REPO", p.repo)
    monkeypatch.setattr(w, "PROJECT", p.name)
    monkeypatch.setattr(w, "PREFIX", "")
    monkeypatch.setattr(w, "TEST_CMD", p.test_cmd)
    monkeypatch.setattr(w, "WORKER", w.WORKER)
    monkeypatch.setattr(w, "ACTIVE_MODEL", w.LITELLM_MODEL)
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "pc_status", lambda: pytest.fail("PC-Status gefragt, obwohl LiteLLM aktiv"))
    monkeypatch.setattr(w, "ollama_free", lambda *a, **k: pytest.fail("Ollama gefragt, obwohl LiteLLM aktiv"))
    monkeypatch.setattr(w, "verwaiste_melden", lambda state, now=None: [])
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "ensure_pre_push_hook", lambda: None)
    monkeypatch.setattr(w, "prepare_issue_frame", lambda n: tmp_path / "acceptance.py")
    monkeypatch.setattr(w, "sync_issue_branch_with_main", lambda n: True)
    monkeypatch.setattr(w, "open_pr", lambda n: True)
    monkeypatch.setattr(w, "check_issue_result", lambda key: None)
    monkeypatch.setattr(w, "run_agent", lambda prompt: 0)
    # Git-Befehle (z. B. main_sha) lesen stdout; ein minimaler CompletedProcess genügt.
    monkeypatch.setattr(w.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(w, "gh", FakeGH())
    monkeypatch.setattr(w.urllib.request, "urlopen",
                        lambda url, timeout: (_ for _ in ()).throw(urllib.error.URLError("kein Netz im Test")))


def test_ausweichen_auf_deepseek_api_nennt_guthaben(lauf, monkeypatch, capsys):
    monkeypatch.setattr(w, "LITELLM_MODEL", "coder-go")
    monkeypatch.setattr(w, "LITELLM_FALLBACKS", ["coder", "coder-or"])
    monkeypatch.setattr(w, "probe_model", lambda m: "HTTP 429" if m == "coder-go" else None)
    assert w.main() == 0
    zeilen = capsys.readouterr().out.splitlines()
    i = next(i for i, z in enumerate(zeilen) if "weiche auf coder aus." in z)
    assert zeilen[i + 1].endswith("Ausweichmodell coder (DeepSeek-API) "
                                  "verbraucht Guthaben statt coder-go")
    assert sum("Ausweichmodell" in z for z in zeilen) == 1


def test_ausweichen_auf_ein_go_modell_schreibt_keinen_hinweis(lauf, monkeypatch, capsys):
    monkeypatch.setattr(w, "LITELLM_MODEL", "coder-go")
    monkeypatch.setattr(w, "LITELLM_FALLBACKS", ["coder-pro-go"])
    monkeypatch.setattr(w, "probe_model", lambda m: "HTTP 429" if m == "coder-go" else None)
    assert w.main() == 0
    out = capsys.readouterr().out
    assert "weiche auf coder-pro-go aus." in out
    assert "Ausweichmodell" not in out


def test_antwortet_das_erste_modell_keine_ausweichzeile(lauf, monkeypatch, capsys):
    monkeypatch.setattr(w, "LITELLM_MODEL", "coder-go")
    monkeypatch.setattr(w, "probe_model", lambda m: None)
    assert w.main() == 0
    out = capsys.readouterr().out
    assert "weiche auf" not in out
    assert "Ausweichmodell" not in out
