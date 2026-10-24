"""Regel 5 des acceptance-guard: `freigabe-geschützt` am verlinkten Issue.

Die Akzeptanztests aus Issue #342 prüfen die Signatur; hier stehen die Fälle, die den
Rest der Verdrahtung absichern: `main()` holt die Issue-Labels, ein GitHub-Fehler wird
nicht verschluckt, und ein Issue-Label entschärft ausschließlich den geänderten
Issue-Text — nicht skip-Markierungen oder fehlende Diffs.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "acceptance_guard.py"
_spec = importlib.util.spec_from_file_location("acceptance_guard_issue_label_unit", _PATH)
g = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = g
_spec.loader.exec_module(g)

ACCEPT = "def test_x():\n    assert 1 + 1 == 2"
BODY = (
    "### Ziel\n\nEtwas.\n\n### Akzeptanztests\n\n```python\n"
    f"{ACCEPT}\n```\n\n### Fertig, wenn\n\n- [ ] grün\n"
)
META = {7: ("2026-10-23T21:54:58Z", "2026-10-23T20:27:37Z")}
LABEL = "freigabe-geschützt"


def _run(labels, issue_labels, files=None):
    return g.evaluate(
        files=files or [],
        labels=labels,
        issues={7: BODY},
        read_head_file=lambda p: ACCEPT,
        issue_meta=META,
        issue_labels=issue_labels,
    )


def test_notiz_nennt_das_label_am_issue_und_die_nummer():
    res = _run(set(), {7: {LABEL}})
    assert res.notes[0] == (
        f"Erlaubt durch Label `{LABEL}` am Issue #7: "
        f"Text von Issue #7 wurde nach der Übernahme (`in-arbeit`, "
        "2026-10-23T20:27:37Z) geändert (2026-10-23T21:54:58Z). "
        "Damit ändert sich der Akzeptanztest."
    )


def test_bericht_zeigt_die_entschaerfung_mit_issue_nummer():
    text = g.report(_run(set(), {7: {LABEL}}))
    assert f"`{LABEL}` am Issue #7" in text
    assert "✅ Keine Verstöße." in text


def test_issue_label_entschaerft_kein_neues_skip():
    # Marker zusammensetzen: stünde er wörtlich in einer Zeile dieser Datei, meldete der
    # Guard das Testdatum selbst als neues skip in seinem eigenen Diff.
    marker = "+@" + "pytest.mark." + "skip(reason='kaputt')"
    files = [{"filename": "tests/test_neu.py", "status": "modified", "patch": marker}]
    res = _run(set(), {7: {LABEL}}, files=files)
    assert not res.ok
    assert any("skip/xfail" in v for v in res.violations)


def test_issue_label_entschaerft_keinen_fehlenden_diff():
    files = [{"filename": "tests/test_gross.py", "status": "modified", "patch": None}]
    res = _run(set(), {7: {LABEL}}, files=files)
    assert not res.ok
    assert any("Kein Diff" in v for v in res.violations)


def test_issue_label_entschaerft_keine_geloeschte_akzeptanzdatei():
    files = [{"filename": "tests/acceptance/test_issue_1.py", "status": "removed"}]
    res = _run(set(), {7: {LABEL}}, files=files)
    assert not res.ok


def test_github_issue_labels_fragt_das_issue_ab():
    gh = object.__new__(g.GitHub)
    pfade = []
    gh._get = lambda path, raw=False: pfade.append(path) or {"labels": [{"name": LABEL}]}
    assert gh.issue_labels(42) == {LABEL}
    assert pfade == ["/issues/42"]


def test_github_issue_labels_ohne_labels_feld_ist_leer():
    gh = object.__new__(g.GitHub)
    gh._get = lambda path, raw=False: {"title": "ohne Labels"}
    assert gh.issue_labels(42) == set()


class _FakeGitHub:
    """Ersetzt die GitHub-Anbindung in `main()`; sammelt die Issue-Nummern."""

    instances = []
    actor = "worker-agent"   # Herkunft des PR-Labels, sofern der Test nichts anderes setzt
    files: list[dict] = []

    def __init__(self, repo, token, api="", graphql_url=""):
        self.label_calls = []
        self.fail_labels = False
        type(self).instances.append(self)

    def issue_body(self, number):
        return number == 7, BODY if number == 7 else None

    def issue_times(self, number):
        return META[number]

    def issue_labels(self, number):
        self.label_calls.append(number)
        if self.fail_labels:
            raise RuntimeError("GitHub nicht erreichbar")
        return {LABEL}

    def label_actor(self, number, label):
        return self.actor

    def pr_files(self, number):
        return self.files

    def file_at(self, path, ref):
        return ACCEPT


def _main(monkeypatch, tmp_path, gh_cls=_FakeGitHub,
          body="Closes #7\n\nWorker: DeepSeek Test", labels=None):
    event = {
        "pull_request": {
            "number": 12,
            "body": body,
            "labels": [{"name": n} for n in (labels or [])],
            "head": {"sha": "abc", "ref": "feature/issue-7"},
        }
    }
    pfad = tmp_path / "event.json"
    pfad.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(pfad))
    monkeypatch.setenv("GITHUB_REPOSITORY", "tripitest-art/agent-sandbox-infra")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(g, "GitHub", gh_cls)
    gh_cls.instances.clear()
    return g.main()


def test_main_holt_die_labels_jedes_verlinkten_echten_issues(monkeypatch, tmp_path):
    assert _main(monkeypatch, tmp_path) == 0
    gh = _FakeGitHub.instances[-1]
    assert gh.label_calls == [7]


def test_main_holt_keine_labels_fuer_verlinkte_prs(monkeypatch, tmp_path):
    class NurPR(_FakeGitHub):
        instances = []

        def issue_body(self, number):
            return False, None

    assert _main(monkeypatch, tmp_path, gh_cls=NurPR) == 0
    assert NurPR.instances[-1].label_calls == []


def test_main_wird_rot_wenn_issue_labels_scheitert(monkeypatch, tmp_path):
    class Kaputt(_FakeGitHub):
        instances = []

        def issue_labels(self, number):
            raise RuntimeError("GitHub nicht erreichbar")

    with pytest.raises(RuntimeError, match="GitHub nicht erreichbar"):
        _main(monkeypatch, tmp_path, gh_cls=Kaputt)


# ---------------- Herkunft des PR-Labels ----------------
def test_label_von_fremdem_konto_gilt_nicht_als_ausnahme(monkeypatch):
    monkeypatch.setenv("GUARD_TRUSTED_ACTORS", "tripitest-art")

    class Fremd:
        def label_actor(self, number, label):
            return "worker-agent"

    labels, note = g.verified_labels(Fremd(), {"number": 12, "labels": [{"name": LABEL}]})
    assert LABEL not in labels
    assert "worker-agent" in note and "tripitest-art" in note


def test_label_von_vertrauenswuerdigem_konto_gilt(monkeypatch):
    monkeypatch.setenv("GUARD_TRUSTED_ACTORS", "tripitest-art")

    class Eigen:
        def label_actor(self, number, label):
            return "tripitest-art"

    labels, note = g.verified_labels(Eigen(), {"number": 12, "labels": [{"name": LABEL}]})
    assert LABEL in labels
    assert "Ausnahme gilt" in note


def test_ohne_trusted_actors_gilt_kein_label(monkeypatch):
    # Leere Variable heißt: niemand. Sonst wäre ein Tippfehler im Workflow eine offene Tür.
    monkeypatch.delenv("GUARD_TRUSTED_ACTORS", raising=False)

    class Eigen:
        def label_actor(self, number, label):
            return "tripitest-art"

    labels, note = g.verified_labels(Eigen(), {"number": 12, "labels": [{"name": LABEL}]})
    assert LABEL not in labels
    assert "niemand" in note


def test_ohne_label_kein_zusaetzlicher_api_aufruf():
    class NieGefragt:
        def label_actor(self, number, label):
            raise AssertionError("label_actor darf ohne Label nicht aufgerufen werden")

    labels, note = g.verified_labels(NieGefragt(), {"number": 12, "labels": []})
    assert note is None
    assert labels == set()


def test_main_verwirft_das_label_eines_fremden_kontos(monkeypatch, tmp_path):
    """Ohne Herkunftsprüfung käme ein Worker mit selbst gesetztem Label an Regel 4 vorbei."""
    monkeypatch.setenv("GUARD_TRUSTED_ACTORS", "tripitest-art")

    class FremdMitInfra(_FakeGitHub):
        instances = []
        actor = "worker-agent"
        files = [{"filename": ".github/workflows/tests.yml", "status": "modified", "patch": "+x"}]

    assert _main(monkeypatch, tmp_path, gh_cls=FremdMitInfra, labels=[LABEL]) == 1


def test_main_erlaubt_das_label_von_stephan(monkeypatch, tmp_path):
    monkeypatch.setenv("GUARD_TRUSTED_ACTORS", "tripitest-art")

    class EigenMitInfra(_FakeGitHub):
        instances = []
        actor = "tripitest-art"
        files = [{"filename": ".github/workflows/tests.yml", "status": "modified", "patch": "+x"}]

    assert _main(monkeypatch, tmp_path, gh_cls=EigenMitInfra, labels=[LABEL]) == 0
