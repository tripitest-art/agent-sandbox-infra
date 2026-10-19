"""Akzeptanztest: Verwaiste in-arbeit-Claims werden auch freigegeben, wenn es keinen Kandidaten gibt."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_orphan_ohne_kandidat", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_orphan_ohne_kandidat"] = w
_spec.loader.exec_module(w)

T0 = 1760880000.0  # feste Epochen-Sekunden
MIN = 60.0


def _iso(sekunden: float) -> str:
    return datetime.fromtimestamp(sekunden, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setattr(w, "WORKER_SLOT", "1")
    monkeypatch.setattr(w, "PREFIX", "")
    projekt = w.projects.default_projects()[0]
    monkeypatch.setattr(w, "PROJECTS", [projekt])
    monkeypatch.setattr(w, "REPO", projekt.repo)
    monkeypatch.setattr(w, "PROJECT", projekt.name)


def _aktives_issue(nummer: int, alter_minuten: float) -> dict:
    return {
        "number": nummer,
        "title": f"Issue {nummer}",
        "body": "### Betroffene Dateien\n\n- `sc_digger/db.py`\n",
        "author": {"login": "tripitest-art"},
        "labels": [{"name": "in-arbeit"}, {"name": "worker-task"}],
        "updatedAt": _iso(T0 - alter_minuten * MIN),
    }


class FakeGH:
    """Kein `bereit`-Kandidat; nur aktive Issues (in-arbeit) und offene PRs."""

    def __init__(self, aktiv, prs=()):
        self.aktiv = list(aktiv)
        self.prs = list(prs)
        self.edits = []

    def __call__(self, *args, **kwargs):
        if args[:2] == ("pr", "list"):
            return list(self.prs)
        if args[:2] == ("issue", "list"):
            return list(self.aktiv) if "in-arbeit" in args else []
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "in-arbeit"}]}
        if args[:2] == ("issue", "edit"):
            self.edits.append(args)
            return ""
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def test_verwaister_claim_ohne_kandidat_wird_freigegeben(monkeypatch, capsys):
    gh = FakeGH([_aktives_issue(324, alter_minuten=50)])
    monkeypatch.setattr(w, "gh", gh)

    assert w.issue_job(now=T0, state={}) is None

    assert len(gh.edits) == 1
    edit = gh.edits[0]
    assert edit[2] == "324"
    assert "bereit" in edit and "in-arbeit" in edit
    assert "⚠ Stephan:" in capsys.readouterr().out


def test_junger_claim_ohne_kandidat_bleibt(monkeypatch):
    gh = FakeGH([_aktives_issue(324, alter_minuten=10)])
    monkeypatch.setattr(w, "gh", gh)

    assert w.issue_job(now=T0, state={}) is None
    assert gh.edits == []


def test_claim_mit_offenem_pr_ohne_kandidat_bleibt(monkeypatch):
    gh = FakeGH([_aktives_issue(324, alter_minuten=50)],
                prs=[{"number": 400, "headRefName": "feature/issue-324"}])
    monkeypatch.setattr(w, "gh", gh)

    assert w.issue_job(now=T0, state={}) is None
    assert gh.edits == []


def test_claim_mit_lebender_belegung_ohne_kandidat_bleibt(monkeypatch):
    import os

    pfad = Path(w.claim_path("issue324"))
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(str(os.getpid()), encoding="utf-8")
    gh = FakeGH([_aktives_issue(324, alter_minuten=50)])
    monkeypatch.setattr(w, "gh", gh)

    assert w.issue_job(now=T0, state={}) is None
    assert gh.edits == []
