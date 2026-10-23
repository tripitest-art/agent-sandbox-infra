import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick_issue_275", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_issue_275"] = r
_spec.loader.exec_module(r)

SHA = "a" * 40
OK_REVIEW = "**Freigegeben**\n\n### Muss\nkeine\n\n### Kann\n- Import nach oben\n\n### Geprüft\n- alles"
GOOD_CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"} for n in ("tests", "acceptance-guard", "CodeQL")]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    p = r.projects.default_projects()[0]
    monkeypatch.setattr(r, "PROJECTS", [p])
    monkeypatch.setattr(r, "REPO", p.repo)
    monkeypatch.setattr(r, "REVIEW_SKILL", p.review_skill)
    monkeypatch.setattr(r, "PROJECT_AUTO_MERGE", p.auto_merge)
    monkeypatch.setattr(r, "PROJECT_MANUAL_PATHS", p.manual_paths)
    monkeypatch.setattr(r, "BASE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "BASE_DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "REVIEW_KEY", "test-key")
    monkeypatch.setattr(r, "REVIEWER_NAME", "GLM")
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "longctx-lite")
    monkeypatch.setattr(r, "POST", False)
    monkeypatch.setattr(r, "AUTO_MERGE", True)
    monkeypatch.setattr(r.worker_tick, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))

    def kein_netz(*a, **k):
        raise urllib.error.URLError("kein Netz im Test")
    monkeypatch.setattr(r.urllib.request, "urlopen", kein_netz)


def _lauf(monkeypatch, antworten, worker="DeepSeek (LiteLLM coder-fast)"):
    """Ein PR mit `worker`; `antworten` bildet den Alias auf Text oder Ausnahme ab."""
    pr = {"number": 7, "title": "Titel", "body": f"Closes #5\n\nWorker: {worker}\n",
          "headRefName": "feature/issue-5", "headRefOid": SHA, "isCrossRepository": False, "reviews": [],
          "commits": [{"committedDate": "2026-10-02T10:00:00Z"}], "statusCheckRollup": GOOD_CHECKS}
    aufrufe = []

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("issue", "view") and "labels" in args:
            return {"labels": [{"name": "agent-qwen"}]}
        if args[:2] == ("issue", "view"):
            return {"number": 5, "title": "Das Issue", "body": "Betroffene Dateien: a.py"}
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 3, "deletions": 0}], "state": "OPEN", "headRefOid": SHA}
        raise AssertionError(args)

    def call_llm(model, messages):
        aufrufe.append(model)
        antwort = antworten[model]
        if isinstance(antwort, Exception):
            raise antwort
        return antwort, {"prompt_tokens": 1000, "completion_tokens": 100}

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/a.py b/a.py\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx" if "AGENTS" in p else "## Schritt 5\nf\n## Schritt 6\nd\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", call_llm)
    monkeypatch.setattr(r, "post_review", lambda n, t: aufrufe.append("gepostet"))
    return aufrufe


def _entwuerfe(tmp_path):
    return list((tmp_path / "reviews").glob("pr7-*.md"))


def test_ausfall_des_ersten_modells_weicht_auf_das_zweite_aus(monkeypatch, tmp_path, capsys):
    aufrufe = _lauf(monkeypatch, {"coder-glm": urllib.error.URLError("geheimer Text"), "longctx-lite": OK_REVIEW})
    assert r.run_once() == 0
    assert aufrufe == ["coder-glm", "longctx-lite"]
    entwurf = _entwuerfe(tmp_path)[0].read_text(encoding="utf-8")
    assert "Reviewer: longctx-lite" in entwurf and "Reviewer: coder-glm" not in entwurf
    log = capsys.readouterr().out
    assert "antwortet nicht (URLError), weiche auf longctx-lite aus" in log
    assert "geheimer Text" not in log


def test_kein_ausweichen_auf_dieselbe_familie_wie_der_worker(monkeypatch, tmp_path, capsys):
    aufrufe = _lauf(monkeypatch, {"coder-glm": urllib.error.URLError("x"), "longctx-lite": OK_REVIEW},
                    worker="Gemini Flash")
    assert r.run_once() == 0
    assert aufrufe == ["coder-glm"]
    assert _entwuerfe(tmp_path) == []
    assert "nächster Takt" in capsys.readouterr().out


def test_kein_ausweichen_wenn_beide_modelle_gleich_sind(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "coder-glm")
    aufrufe = _lauf(monkeypatch, {"coder-glm": urllib.error.URLError("x")})
    assert r.run_once() == 0
    assert aufrufe == ["coder-glm"]
    assert _entwuerfe(tmp_path) == []


def test_faellt_auch_das_zweite_modell_aus_gibt_der_takt_auf(monkeypatch, tmp_path, capsys):
    aufrufe = _lauf(monkeypatch, {"coder-glm": urllib.error.URLError("x"), "longctx-lite": OSError("y")})
    assert r.run_once() == 0
    assert aufrufe == ["coder-glm", "longctx-lite"]
    assert _entwuerfe(tmp_path) == []
    log = capsys.readouterr().out
    assert "Modell longctx-lite antwortet nicht (OSError), nächster Takt" in log


def test_ohne_ausfall_bleibt_es_bei_einem_aufruf(monkeypatch, tmp_path):
    aufrufe = _lauf(monkeypatch, {"coder-glm": OK_REVIEW, "longctx-lite": OK_REVIEW})
    assert r.run_once() == 0
    assert aufrufe == ["coder-glm"]
    assert len(_entwuerfe(tmp_path)) == 1
