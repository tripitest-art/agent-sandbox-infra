import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "review_tick.py"
_spec = importlib.util.spec_from_file_location("review_tick", _PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick"] = r
_spec.loader.exec_module(r)

SHA = "a" * 40
LABEL = "review-anfordern"
OK_BOLD = "**Freigegeben**\n\n### Muss\nkeine\n\n### Kann\n- Import nach oben\n\n### Geprüft\n- alles"
OK_PLAIN = "Freigegeben\n\n### Muss\nkeine\n\n### Kann\n- Import nach oben\n\n### Geprüft\n- alles"
LENGTH = ("", {"prompt_tokens": 13000, "completion_tokens": 16000, "finish_reason": "length"})
OK_USAGE = {"prompt_tokens": 1000, "completion_tokens": 100, "finish_reason": "stop"}
CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": "2026-10-17T08:00:00Z"}
          for n in ("tests", "acceptance-guard")]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    p = r.projects.default_projects()[0]
    monkeypatch.setattr(r, "PROJECTS", [p])
    monkeypatch.setattr(r, "REPO", p.repo)
    monkeypatch.setattr(r, "REVIEW_SKILL", p.review_skill)
    monkeypatch.setattr(r, "PROJECT_AUTO_MERGE", False)
    monkeypatch.setattr(r, "REQUIRED_CHECKS", ("tests", "acceptance-guard"))
    monkeypatch.setattr(r, "BASE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "BASE_DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(r, "DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "REVIEW_KEY", "test-key")
    monkeypatch.setattr(r, "REVIEWER_NAME", "GLM")
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "longctx-lite")
    monkeypatch.setattr(r, "POST", True)
    monkeypatch.setattr(r, "AUTO_MERGE", True)

    def no_network(*a, **k):
        raise urllib.error.URLError("kein Netz im Test")
    monkeypatch.setattr(r.urllib.request, "urlopen", no_network)


def test_fallback_model_ist_das_jeweils_andere_modell():
    assert r.fallback_model("coder-glm") == "longctx-lite"
    assert r.fallback_model("longctx-lite") == "coder-glm"


def test_fallback_model_gibt_es_nicht_bei_gleichem_modell(monkeypatch):
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "coder-glm")
    assert r.fallback_model("coder-glm") is None


def test_normalize_verdict_line_macht_aus_dem_urteil_die_feste_form():
    assert r.normalize_verdict_line(OK_PLAIN) == OK_BOLD
    assert r.normalize_verdict_line("Änderungen nötig.\n\n### Muss\n1. x") == "**Änderungen nötig**\n\n### Muss\n1. x"
    assert r.normalize_verdict_line("\n  freigegeben  \n\nrest") == "\n**Freigegeben**\n\nrest"
    assert r.normalize_verdict_line("__Freigegeben__\nrest") == "**Freigegeben**\nrest"


def test_normalize_verdict_line_laesst_alles_andere_unveraendert():
    for text in (OK_BOLD, "Freigegeben, aber mit Vorbehalt\nrest", "Ich würde freigeben.\nrest", "", "### Muss\nkeine"):
        assert r.normalize_verdict_line(text) == text


def _pr(worker="DeepSeek (LiteLLM coder)", **kw):
    pr = {"number": 7, "title": "PR 7", "body": f"Was und warum\n\nWorker: {worker}\n", "headRefName": "feature/manuell-7",
          "headRefOid": SHA, "isCrossRepository": False, "isDraft": False, "labels": [{"name": LABEL}],
          "reviews": [], "commits": [{"committedDate": "2026-10-17T07:00:00Z"}], "statusCheckRollup": CHECKS}
    pr.update(kw)
    return pr


def _takt(monkeypatch, antworten, worker="DeepSeek (LiteLLM coder)"):
    """Ein Takt für PR #7. `antworten`: Modellalias -> (Text, usage). Liefert Aufrufe, Posts, Zustand."""
    seen = {"calls": [], "posted": [], "pr": _pr(worker)}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [seen["pr"]]
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 1, "deletions": 0}]}
        if args[:2] == ("issue", "list"):
            return []
        raise AssertionError(args)

    def call_llm(model, messages):
        seen["calls"].append(model)
        return antworten[model]

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/a.py b/a.py\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", call_llm)
    monkeypatch.setattr(r, "post_review", lambda n, t: seen["posted"].append(t))
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: True)
    monkeypatch.setattr(r, "to_stephan", lambda n, key, reason, state: None)
    assert r.run_once() == 0
    return seen


def _fehler(tmp_path):
    import json
    p = tmp_path / "state.json"
    return json.loads(p.read_text(encoding="utf-8")).get("fehler", {}) if p.exists() else {}


def test_token_limit_beim_ersten_modell_geht_im_selben_takt_an_das_zweite(monkeypatch, tmp_path):
    seen = _takt(monkeypatch, {"coder-glm": LENGTH, "longctx-lite": (OK_PLAIN, OK_USAGE)})
    assert seen["calls"] == ["coder-glm", "longctx-lite"]
    assert len(seen["posted"]) == 1
    text = seen["posted"][0]
    assert text.startswith("**Freigegeben**")            # Urteilszeile in fester Form, auch wenn das Modell sie ohne Sterne schrieb
    assert "Reviewer: longctx-lite" in text             # genannt wird das Modell, das wirklich geantwortet hat
    assert _fehler(tmp_path) == {}                      # kein verbrauchter Fehlversuch


def test_antwort_ohne_urteil_geht_ebenfalls_an_das_zweite_modell(monkeypatch, tmp_path):
    seen = _takt(monkeypatch, {"coder-glm": ("Hier ist mein Eindruck, ohne Urteil.", OK_USAGE),
                               "longctx-lite": (OK_BOLD, OK_USAGE)})
    assert seen["calls"] == ["coder-glm", "longctx-lite"] and len(seen["posted"]) == 1


def test_gute_antwort_ruft_nur_ein_modell(monkeypatch):
    seen = _takt(monkeypatch, {"coder-glm": (OK_BOLD, OK_USAGE)})
    assert seen["calls"] == ["coder-glm"] and len(seen["posted"]) == 1


def test_urteil_ohne_sterne_vom_ersten_modell_wird_gepostet(monkeypatch):
    seen = _takt(monkeypatch, {"coder-glm": (OK_PLAIN, OK_USAGE)})
    assert seen["calls"] == ["coder-glm"]
    assert seen["posted"][0].startswith("**Freigegeben**")


def test_kein_ausweichen_auf_ein_modell_der_familie_des_workers(monkeypatch, tmp_path, capsys):
    # Worker aus der Gemini-Familie: longctx-lite wäre nicht unabhängig, also kein Ausweichen.
    seen = _takt(monkeypatch, {"coder-glm": LENGTH, "longctx-lite": (OK_BOLD, OK_USAGE)}, worker="Gemini Flash")
    assert seen["calls"] == ["coder-glm"] and seen["posted"] == []
    assert _fehler(tmp_path)[f"pr7@{SHA}"] == 1
    assert "Versuch 1" in capsys.readouterr().out


def test_ohne_zweites_modell_bleibt_es_beim_fehlversuch(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "coder-glm")
    seen = _takt(monkeypatch, {"coder-glm": LENGTH})
    assert seen["calls"] == ["coder-glm"] and seen["posted"] == []
    assert _fehler(tmp_path)[f"pr7@{SHA}"] == 1


def test_scheitern_beide_modelle_zaehlt_ein_fehlversuch(monkeypatch, tmp_path, capsys):
    seen = _takt(monkeypatch, {"coder-glm": LENGTH, "longctx-lite": ("kein Urteil", OK_USAGE)})
    assert seen["calls"] == ["coder-glm", "longctx-lite"] and seen["posted"] == []
    assert _fehler(tmp_path)[f"pr7@{SHA}"] == 1
    assert "weiche auf longctx-lite aus" in capsys.readouterr().out
