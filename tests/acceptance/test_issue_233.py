from pathlib import Path
import subprocess
import pytest

from sandbox import worker_frame as wf

FENCE = "`" * 3
FORM_ISSUE = (
    "### Ziel\n\nDeterministischen Rahmen testen.\n\n"
    "### Betroffene Dateien\n\n- `sandbox/worker_frame.py`\n\n"
    "### Akzeptanztests\n\n"
    + FENCE + "python\ndef test_beispiel():\n    assert 1 + 1 == 2\n" + FENCE + "\n\n"
    "### Fertig, wenn\n\n- [ ] grün\n"
)

TEMPLATE = (
    "## Was und warum\n\n"
    "<!-- Pflicht: \"Closes #<Issue>\" -->\n"
    "Closes #\n\n"
    "## Wie getestet\n\n"
    "- [ ] `python -m pytest -q` (alle grün)\n"
    "- [ ] Neue Tests für die Änderung\n\n"
    "## Offene Punkte\n\n"
    "<!-- \"keine\" reicht -->\n\n"
    "## Umgesetzt von / Review durch\n\n"
    "<!-- Pflicht: \"Worker: <Familie>\" -->\n"
)


def test_extract_acceptance_block_liest_ersten_codeblock():
    code = wf.extract_acceptance_block(FORM_ISSUE)
    assert code == "def test_beispiel():\n    assert 1 + 1 == 2"


def test_extract_acceptance_block_gibt_none_ohne_block():
    assert wf.extract_acceptance_block("### Ziel\n\nNur Text\n") is None
    assert wf.extract_acceptance_block("### Akzeptanztests\n\n_No response_\n") is None
    assert wf.extract_acceptance_block(None) is None


def test_setup_issue_branch_schreibt_test_und_committet(tmp_path):
    calls = []

    def fake_run(cmd):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    test_path = wf.setup_issue_branch(tmp_path, 42, FORM_ISSUE, fake_run)
    expected = tmp_path / "tests" / "acceptance" / "test_issue_42.py"
    assert test_path == expected
    assert expected.read_text(encoding="utf-8") == "def test_beispiel():\n    assert 1 + 1 == 2\n"
    assert calls == [
        ["git", "checkout", "-B", "feature/issue-42"],
        ["git", "add", "tests/acceptance/test_issue_42.py"],
        ["git", "commit", "-m", "Akzeptanztests aus #42"],
    ]


def test_setup_issue_branch_ohne_akzeptanztest_wechselt_nur_branch(tmp_path):
    calls = []

    def fake_run(cmd):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    res = wf.setup_issue_branch(tmp_path, 99, "### Ziel\n\nOhne Block\n", fake_run)
    assert res is None
    assert calls == [["git", "checkout", "-B", "feature/issue-99"]]


def test_setup_issue_branch_wirft_bei_git_fehler(tmp_path):
    def fail_run(cmd):
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: git kaputt")

    with pytest.raises(RuntimeError):
        wf.setup_issue_branch(tmp_path, 42, FORM_ISSUE, fail_run)


def test_build_pr_body_erfuellt_vorlage_und_worker_zeile():
    body = wf.build_pr_body(TEMPLATE, 42, "DeepSeek (LiteLLM coder)", summary="Rahmen ergänzt.")
    assert "<!--" not in body
    assert "Closes #42" in body
    assert "Rahmen ergänzt." in body
    assert "Worker: DeepSeek (LiteLLM coder)" in body
    for heading in ("## Was und warum", "## Wie getestet", "## Offene Punkte", "## Umgesetzt von / Review durch"):
        assert heading in body
