"""Tests für sandbox/worker_frame.py: Akzeptanztest-Block, Branch-Rahmen und PR-Text."""

import subprocess
from pathlib import Path

import pytest

from sandbox import worker_frame as wf
from sandbox import worker_tick as wt

REPO_ROOT = Path(__file__).resolve().parents[1]
FENCE = "`" * 3


def _issue(section: str, level: str = "###") -> str:
    return f"### Ziel\n\nNur Text.\n\n{level} Akzeptanztests\n\n{section}\n\n### Fertig, wenn\n\n- [ ] grün\n"


def _runner(calls, fail_on=None):
    def run(cmd):
        calls.append(list(cmd))
        code = 128 if fail_on and cmd[1:2] == [fail_on] else 0
        return subprocess.CompletedProcess(cmd, code, "", "fatal: kaputt" if code else "")

    return run


def test_extract_normalisiert_crlf_und_leerzeilen():
    body = _issue(f"\r\n{FENCE}python\r\n\r\ndef test_x():\r\n    assert True   \r\n\r\n{FENCE}\r\n")
    assert wf.extract_acceptance_block(body) == "def test_x():\n    assert True"


def test_extract_liest_tilde_fences():
    body = _issue(f"~~~python\nprint('hallo')\n~~~")
    assert wf.extract_acceptance_block(body) == "print('hallo')"


def test_extract_ueberspringt_leeren_ersten_block():
    body = _issue(f"{FENCE}\n\n{FENCE}\n\n{FENCE}\nwert\n{FENCE}")
    assert wf.extract_acceptance_block(body) == "wert"


def test_extract_endet_an_gleichrangiger_ueberschrift():
    body = (
        "#### Akzeptanztests\n\n_No response_\n\n"
        f"#### Fertig, wenn\n\n{FENCE}\nzu spaet\n{FENCE}\n"
    )
    assert wf.extract_acceptance_block(body) is None


def test_extract_ignoriert_ueberschrift_im_codeblock():
    body = _issue(f"{FENCE}python\n# kein Abschnittsende\nx = 1\n{FENCE}")
    assert wf.extract_acceptance_block(body) == "# kein Abschnittsende\nx = 1"


def test_extract_ohne_schliessenden_zaun_gibt_none():
    body = _issue(f"{FENCE}python\nx = 1\n")
    assert wf.extract_acceptance_block(body) is None


def test_setup_issue_branch_schreibt_auch_bei_crlf_genau_einen_umbruch(tmp_path):
    calls = []
    body = _issue(f"{FENCE}python\r\ndef test_x():\r\n    pass\r\n{FENCE}")
    target = wf.setup_issue_branch(tmp_path, 7, body, _runner(calls))
    assert target == tmp_path / "tests" / "acceptance" / "test_issue_7.py"
    assert target.read_text(encoding="utf-8") == "def test_x():\n    pass\n"


def test_setup_issue_branch_wirft_bei_commit_fehler(tmp_path):
    calls = []
    with pytest.raises(RuntimeError, match="commit"):
        wf.setup_issue_branch(tmp_path, 8, _issue(f"{FENCE}\nx = 1\n{FENCE}"), _runner(calls, fail_on="commit"))
    # Branch und Datei sind schon angelegt, erst der Commit scheitert.
    assert calls == [
        ["git", "checkout", "-B", "feature/issue-8"],
        ["git", "add", "tests/acceptance/test_issue_8.py"],
        ["git", "commit", "-m", "Akzeptanztests aus #8"],
    ]
    assert (tmp_path / "tests" / "acceptance" / "test_issue_8.py").exists()


def test_setup_issue_branch_meldet_git_fehler_mit_stderr(tmp_path):
    with pytest.raises(RuntimeError, match="fatal: kaputt"):
        wf.setup_issue_branch(tmp_path, 9, None, _runner([], fail_on="checkout"))


def test_build_pr_body_leeres_template_hat_pflichtabschnitte():
    body = wf.build_pr_body("", 5, "Family Model")
    assert "Closes #5" in body
    for heading in ("## Was und warum", "## Wie getestet", "## Offene Punkte", "## Umgesetzt von / Review durch"):
        assert heading in body
    assert "keine" in body
    assert "Worker: Family Model" in body


def test_build_pr_body_ergaenzt_closes_ohne_verweis():
    template = "## Was und warum\n\nText ohne Verweis.\n\n## Offene Punkte\n"
    body = wf.build_pr_body(template, 11, "Family Model")
    assert body.index("Closes #11") < body.index("## Offene Punkte")
    assert "Closes #" not in body.replace("Closes #11", "")


def test_build_pr_body_summary_bleibt_woertlich():
    summary = r"Pfad C:\temp\1 und Gruppe \g<0>"
    body = wf.build_pr_body("## Was und warum\n\nCloses #\n", 12, "Family Model", summary=summary)
    assert summary in body
    assert body.count("Closes #12") == 1


def test_build_pr_body_behaelt_ueberschriften_der_vorlage():
    template = (REPO_ROOT / wt.PR_TEMPLATE).read_text(encoding="utf-8")
    body = wf.build_pr_body(template, 233, wt.WORKER, summary="Rahmen ergänzt.")
    for line in template.splitlines():
        if line.startswith("## "):
            assert line.strip() in body
    # Abschnitte bleiben durch Leerzeilen getrennt, „keine“ steht als eigener Absatz.
    assert "\n\n## Wie getestet" in body
    assert "\nkeine\n\n## Umgesetzt von / Review durch" in body


def test_build_pr_body_besteht_pr_text_problems():
    template = (REPO_ROOT / wt.PR_TEMPLATE).read_text(encoding="utf-8")
    body = wf.build_pr_body(template, 233, wt.WORKER, summary="Rahmen ergänzt.")
    assert wt.pr_text_problems(body, 233) == []
