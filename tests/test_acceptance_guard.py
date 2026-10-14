"""Tests für .github/scripts/acceptance_guard.py (reine Logik, kein Netzwerk)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Das Skript liegt bewusst unter .github/ (geschützt) und ist kein Paket, daher per Pfad laden.
# Registrierung in sys.modules ist nötig, sonst scheitern die Dataclasses beim Import.
_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "acceptance_guard.py"
_spec = importlib.util.spec_from_file_location("acceptance_guard", _PATH)
g = importlib.util.module_from_spec(_spec)
sys.modules["acceptance_guard"] = g
_spec.loader.exec_module(g)

ACCEPT = "def test_x():\n    assert 1 + 1 == 2"

# So rendert GitHub ein Issue-Formular mit render: python
FORM_BODY = f"""### Ziel

Etwas besser machen.

### Akzeptanztests

```python
{ACCEPT}
```

### Fertig, wenn

- [ ] grün
"""


def _file(name, status="modified", patch="", old=None):
    d = {"filename": name, "status": status, "patch": patch}
    if old:
        d["previous_filename"] = old
    return d


def _reader(files: dict):
    return lambda p: files.get(p)


# ---------------- Parsing ----------------
@pytest.mark.parametrize("body, expected", [
    ("Closes #8", [8]),
    ("fixes #3 und resolves: #7", [3, 7]),
    ("Closed #2, closes #2", [2]),
    ("Siehe #9 (nur erwähnt)", []),
    (None, []),
])
def test_linked_issues(body, expected):
    assert g.linked_issues(body) == expected


def test_extract_block_from_issue_form():
    assert g.extract_acceptance_block(FORM_BODY) == ACCEPT


def test_extract_block_empty_form_field_is_none():
    body = "### Akzeptanztests\n\n_No response_\n\n### Kontext\n\nx"
    assert g.extract_acceptance_block(body) is None


def test_extract_block_ignores_code_of_other_sections():
    body = "### Schnittstellen\n\n```python\ndef f(): ...\n```\n\n### Akzeptanztests\n\n_No response_\n"
    assert g.extract_acceptance_block(body) is None


def test_extract_block_handwritten_h2_and_crlf():
    body = "## Akzeptanztests\r\n\r\n```python\r\n" + ACCEPT.replace("\n", "\r\n") + "\r\n```\r\n"
    assert g.extract_acceptance_block(body) == ACCEPT


def test_heading_inside_code_does_not_end_section():
    code = "# Kommentar im Test\ndef test_y():\n    pass"
    body = f"### Akzeptanztests\n\n```python\n{code}\n```\n"
    assert g.extract_acceptance_block(body) == code


def test_normalize_ignores_trailing_whitespace_and_line_endings():
    assert g.normalize("a  \r\nb\r\n\r\n") == g.normalize("a\nb")


def test_added_skip_markers_only_counts_added_lines():
    patch = "\n".join([
        "+++ b/tests/test_a.py",
        "+@pytest.mark.skip(reason='kaputt')",
        "-@pytest.mark.xfail",
        " pytest.skip('schon da')",
        "+    pytest.importorskip('librosa')",
        "+def test_skips_deleted_tracks():",
    ])
    assert g.added_skip_markers(patch) == [
        "@pytest.mark.skip(reason='kaputt')",
        "pytest.importorskip('librosa')",
    ]


# ---------------- Bewertung ----------------
def test_clean_worker_pr_passes():
    files = [
        _file("sc_digger/organize.py"),
        _file("tests/acceptance/test_issue_8.py", "added"),
    ]
    res = g.evaluate(files, set(), {8: FORM_BODY},
                     _reader({"tests/acceptance/test_issue_8.py": ACCEPT + "\n"}))
    assert res.ok, res.violations


def test_missing_acceptance_file_fails_even_with_override():
    res = g.evaluate([_file("sc_digger/organize.py")], {g.OVERRIDE_LABEL}, {8: FORM_BODY}, _reader({}))
    assert not res.ok
    assert "fehlt" in res.violations[0]


def test_weakened_acceptance_test_fails_with_diff():
    weakened = "def test_x():\n    assert True"
    res = g.evaluate([_file("tests/acceptance/test_issue_8.py", "added")], set(), {8: FORM_BODY},
                     _reader({"tests/acceptance/test_issue_8.py": weakened}))
    assert not res.ok
    assert "+    assert True" in res.violations[0]


def test_issue_without_acceptance_block_is_only_a_note():
    res = g.evaluate([_file("sc_digger/x.py")], set(), {5: "### Ziel\n\nx"}, _reader({}))
    assert res.ok and res.notes


@pytest.mark.parametrize("entry", [
    _file("tests/acceptance/test_issue_3.py", "modified"),
    _file("tests/acceptance/test_issue_3.py", "removed"),
    _file("tests/test_old.py", "renamed", old="tests/acceptance/test_issue_3.py"),
])
def test_existing_acceptance_tests_are_immutable(entry):
    assert not g.evaluate([entry], set(), {}, _reader({})).ok


@pytest.mark.parametrize("name", [
    ".github/workflows/tests.yml",
    ".github/scripts/acceptance_guard.py",
    "tests/conftest.py",
    "conftest.py",
    "pytest.ini",
    "pyproject.toml",
])
def test_infra_changes_need_override(name):
    assert not g.evaluate([_file(name)], set(), {}, _reader({})).ok
    res = g.evaluate([_file(name)], {g.OVERRIDE_LABEL}, {}, _reader({}))
    assert res.ok and "Erlaubt" in res.notes[0]


def test_new_skip_in_tests_needs_override():
    f = _file("tests/test_core.py", patch="+@pytest.mark.xfail(reason='später')")
    assert not g.evaluate([f], set(), {}, _reader({})).ok
    assert g.evaluate([f], {g.OVERRIDE_LABEL}, {}, _reader({})).ok


def test_ordinary_code_and_test_changes_pass():
    files = [
        _file("sc_digger/pipeline.py", patch="+x = 1"),
        _file("tests/test_core.py", patch="+def test_new():\n+    assert True"),
        _file("README.md"),
    ]
    assert g.evaluate(files, set(), {}, _reader({})).ok


# ---------------- Issue-Text nach der Übernahme (issue_times, GraphQL gefakt) ----------------
class _FakeResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_graphql(monkeypatch, payload: dict) -> list:
    """Ersetzt urlopen im Guard; gibt die Liste der abgeschickten Requests zurück."""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(req)
        return _FakeResponse(payload)

    monkeypatch.setattr(g.urllib.request, "urlopen", fake_urlopen)
    return sent


def _issue_payload(last_edited, events):
    nodes = [{"createdAt": ts, "label": {"name": name}} for ts, name in events]
    return {"data": {"repository": {"issue": {
        "lastEditedAt": last_edited,
        "timelineItems": {"nodes": nodes},
    }}}}


def test_issue_times_takes_earliest_claim_and_ignores_other_labels(monkeypatch):
    sent = _fake_graphql(monkeypatch, _issue_payload("2026-09-03T12:00:00Z", [
        ("2026-09-01T08:00:00Z", "bereit"),
        ("2026-09-02T10:00:00Z", "in-arbeit"),  # erneut gesetzt, zählt nicht
        ("2026-09-01T09:00:00Z", "in-arbeit"),  # frühestes, auch wenn nicht zuerst geliefert
        ("2026-08-31T09:00:00Z", "worker-task"),
    ]))
    gh = g.GitHub("tripitest-art/sc-digger", "tok", graphql_url="https://ghe.example/graphql")

    assert gh.issue_times(87) == ("2026-09-03T12:00:00Z", "2026-09-01T09:00:00Z")

    req = sent[0]
    assert req.full_url == "https://ghe.example/graphql"
    assert req.get_method() == "POST"
    body = json.loads(req.data)
    assert body["variables"] == {"owner": "tripitest-art", "name": "sc-digger", "number": 87}


def test_issue_times_without_claim_event_is_none(monkeypatch):
    _fake_graphql(monkeypatch, _issue_payload(None, [("2026-09-01T08:00:00Z", "bereit")]))
    gh = g.GitHub("tripitest-art/sc-digger", "tok")
    assert gh.issue_times(87) == (None, None)


def test_issue_times_graphql_error_is_not_swallowed(monkeypatch):
    # GraphQL meldet Fehler mit HTTP 200; der Check muss dann rot werden, nicht still grün.
    _fake_graphql(monkeypatch, {"data": None, "errors": [{"message": "kaputt"}]})
    gh = g.GitHub("tripitest-art/sc-digger", "tok")
    with pytest.raises(RuntimeError):
        gh.issue_times(87)


def test_never_claimed_issue_can_be_allowed_by_override():
    res = g.evaluate([_file("tests/acceptance/test_issue_8.py", "added")], {g.OVERRIDE_LABEL},
                     {8: FORM_BODY}, _reader({"tests/acceptance/test_issue_8.py": ACCEPT}),
                     issue_meta={8: (None, None)})
    assert res.ok
    assert any("#8" in n and g.OVERRIDE_LABEL in n for n in res.notes)


# ---------------- Regel 6: PR-Text ----------------
TEMPLATE = (Path(__file__).resolve().parents[1] / ".github" / "pull_request_template.md").read_text(
    encoding="utf-8")

FILLED = """## Was und warum

Closes #100

## Umgesetzt von / Review durch

Worker: Qwen3-Coder 30B
"""


def test_unfilled_template_on_worker_branch_fails():
    # So kam PR #103: Vorlage unverändert, der Check lief trotzdem grün.
    found = g.pr_text_violations("feature/issue-100-camelot-distance", TEMPLATE)
    assert len(found) == 3
    assert any("ohne Issue-Nummer" in v for v in found)
    assert any("Closes #100" in v for v in found)
    assert any("Worker" in v for v in found)


def test_filled_worker_pr_passes():
    assert g.pr_text_violations("feature/issue-100-camelot-distance", FILLED) == []


def test_worker_branch_must_link_its_own_issue():
    body = FILLED.replace("Closes #100", "Closes #99")
    found = g.pr_text_violations("feature/issue-100-x", body)
    assert found == ["Worker-Branch `feature/issue-100-x`, aber der PR-Text enthält kein „Closes #100“."]


@pytest.mark.parametrize("line", [
    "Worker: Gemini Flash",
    "- Worker: Qwen 3.5",
    "**Worker:** Claude Opus",
    "worker: gpt-oss 20b",
])
def test_worker_line_variants(line):
    body = f"Closes #7\n\n{line}\n"
    assert g.pr_text_violations("feature/issue-7-kurz", body) == []


def test_worker_line_only_in_template_comment_does_not_count():
    body = 'Closes #7\n\n<!-- z. B. "Worker: Gemini Flash" -->\n'
    found = g.pr_text_violations("feature/issue-7-kurz", body)
    assert found == ["PR-Text nennt keinen Worker („Worker: <Familie> <Modell>“, AGENTS.md Worker 7)."]


def test_bare_closes_fails_on_any_branch():
    found = g.pr_text_violations("docs/irgendwas", "Closes #\n\nDoku.")
    assert found == ["PR-Text enthält „Closes #“ ohne Issue-Nummer (Vorlage nicht ausgefüllt)."]


def test_non_worker_branch_needs_no_issue_or_worker():
    assert g.pr_text_violations("docs/planer", "Doku-PR ohne Worker-Issue, prüft Stephan.") == []
    assert g.pr_text_violations(None, None) == []


def test_template_comment_mentioning_closes_is_ignored():
    body = '<!-- Pflicht: "Closes #<Issue>" -->\nDoku.'
    assert g.pr_text_violations("docs/x", body) == []
