"""Akzeptanztests für Issue <N>: Unabhängigkeitsregel fällt nicht mehr offen aus."""
import importlib.util
import sys
from pathlib import Path


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


r = _load("review_tick", "sandbox/review_tick.py")
w = _load("worker_tick", "sandbox/worker_tick.py")


def test_every_worker_name_has_a_family():
    for alias, name in w.WORKER_NAMES.items():
        assert r.worker_family(name), f"Worker-Name {name!r} ({alias}) ist keiner Familie zuzuordnen"


def test_unknown_family_blocks_the_review():
    # Früher: `if wf and wf == rf` – ohne erkennbare Worker-Familie lief der Review trotzdem.
    assert r.independence_problem("Rätselmodell 7", "coder-glm")


def test_same_family_blocks_other_family_passes():
    assert r.independence_problem("GLM 5.3 Flash (LiteLLM coder-glm)", "coder-glm")
    assert r.independence_problem("DeepSeek (LiteLLM coder-fast)", "coder-glm") is None


def test_reviewer_config_must_be_known_and_of_one_family(monkeypatch):
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "unbekannt-7b")
    monkeypatch.setattr(r, "REVIEWER_NAME", "GLM")
    assert r.reviewer_config_problem()          # Alias ohne Familien-Schlüsselwort
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "coder-glm")
    assert r.reviewer_config_problem() is None
    monkeypatch.setattr(r, "REVIEWER_NAME", "Mistral Large")
    assert r.reviewer_config_problem()          # Name passt nicht zu den aufgerufenen Modellen


def test_reviewer_model_follows_the_diff_size(monkeypatch):
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "longctx-lite")
    assert r.reviewer_model("x" * (r.BIG_DIFF_CHARS + 1)) == "longctx-lite"
    assert r.reviewer_model("x" * r.BIG_DIFF_CHARS) == "coder-glm"


def test_assemble_names_the_model_actually_called():
    text = r.assemble("**Freigegeben**\n\n### Muss\nkeine\n", "DeepSeek (LiteLLM coder)",
                      [("tests", "SUCCESS")], "longctx-lite")
    assert "Reviewer: longctx-lite" in text
    assert "Reviewer: GLM" not in text
