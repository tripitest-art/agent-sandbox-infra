import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "acceptance_guard.py"
ACCEPT = "def test_x():\n    assert 1 + 1 == 2"
FENCE = "`" * 3  # kein Dreifach-Backtick im Block: der Guard liest bis zum ersten schließenden Zaun
BODY = f"### Ziel\n\nEtwas.\n\n### Akzeptanztests\n\n{FENCE}python\n{ACCEPT}\n{FENCE}\n\n### Fertig, wenn\n\n- [ ] grün\n"
META = {7: ("2026-10-23T21:54:58Z", "2026-10-23T20:27:37Z")}
LABEL = "freigabe-geschützt"


def _guard():
    spec = importlib.util.spec_from_file_location("acceptance_guard_issue_label", _PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(labels, issue_labels, files=None):
    g = _guard()
    return g.evaluate(
        files=files or [],
        labels=labels,
        issues={7: BODY},
        read_head_file=lambda p: ACCEPT,
        issue_meta=META,
        issue_labels=issue_labels,
    )


def test_ohne_label_bleibt_regel_5_rot():
    res = _run(set(), {7: set()})
    assert not res.ok
    assert any("nach der Übernahme" in v for v in res.violations)


def test_label_am_issue_entschaerft_regel_5():
    res = _run(set(), {7: {LABEL}})
    assert res.ok
    assert any(LABEL in n and "#7" in n and "Issue" in n for n in res.notes)


def test_label_an_anderem_issue_hilft_nicht():
    res = _run(set(), {8: {LABEL}})
    assert not res.ok


def test_label_am_pr_funktioniert_weiter_ohne_issue_labels():
    assert _run({LABEL}, None).ok


def test_issue_label_entschaerft_keine_ci_aenderung():
    files = [{"filename": ".github/workflows/tests.yml", "status": "modified", "patch": "+x"}]
    res = _run(set(), {7: {LABEL}}, files=files)
    assert not res.ok
    assert any(".github" in v for v in res.violations)


def test_issue_label_entschaerft_keinen_geaenderten_akzeptanztest():
    files = [{"filename": "tests/acceptance/test_issue_1.py", "status": "modified", "patch": "+x"}]
    res = _run(set(), {7: {LABEL}}, files=files)
    assert not res.ok


def test_github_issue_labels_liest_die_label_namen():
    g = _guard()
    gh = object.__new__(g.GitHub)
    gh._get = lambda path, raw=False: {"labels": [{"name": LABEL}, {"name": "phase-1"}]}
    assert gh.issue_labels(7) == {LABEL, "phase-1"}


def test_github_issue_labels_fehlendes_issue_ist_leer():
    g = _guard()
    gh = object.__new__(g.GitHub)
    gh._get = lambda path, raw=False: None
    assert gh.issue_labels(7) == set()
