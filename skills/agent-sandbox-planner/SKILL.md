---
name: agent-sandbox-planner
description: "Plan and verify worker issues for agent-sandbox-infra (planner role). Use when prompted with 'Plan ...', 'Create an issue for ...', or 'Turn this into a task'. Drafts contract-compliant worker issues, runs pre-flight validation with scripts/issuecheck.py, and creates the issue upon approval."
always-apply: true
---

# Planner Skill: agent-sandbox-infra

For all planner agents, regardless of model family. Fundamental rules are defined in `AGENTS.md`
(section „Worker-Aufgaben → Planer“) and the detailed process in `docs/planer-ablauf.md`.

The Planner writes issues, never code: no branches, no commits, no PRs, no merges.

---

## 8-Step Workflow

### Step 1: Clarify Requirements
- Clarify goal in one or two sentences.
- Explicitly list non-goals („Nicht Teil dieser Aufgabe“).
- Only one topic per issue; split larger tasks into manageable parts („Part 1 of #X“).
- Clarify open domain questions with Stephan; never guess.

### Step 2: Check Status and Conflicts (Rule 8)
- Search open issues and PRs for overlaps:
  - `gh pr list --repo tripitest-art/agent-sandbox-infra --state open`
  - `gh issue list --repo tripitest-art/agent-sandbox-infra --state open --label in-arbeit`
- If an open PR touches the same files: add label `blockiert` and a line `Wartet auf: #X` under context.
- **Dependency trap warning (#46):** Never include a `Wartet auf: #<number>` line inside acceptance test code blocks or comments, as the review loop parses those lines as real dependencies.

### Step 3: Inspect Code
- Read every file planned for modification on the latest `main` branch.
- Reuse existing test helpers and patterns from `tests/test_worker_tick.py`, `tests/test_acceptance_guard.py`, or `tests/test_projects.py`.
- Check existing acceptance tests (`git grep <function> tests/acceptance`): workers must never weaken or break them.

### Step 4: Structure Issue Text
The issue body must strictly follow the format in `.github/ISSUE_TEMPLATE/worker-task.yml`:
1. `### Ziel`
2. `### Betroffene Dateien` (complete list; always includes: `tests/acceptance/test_issue_<N>.py` and dedicated test file)
3. `### Schnittstellen` (in exactly one ```` ```python ```` block with exact types and signatures)
4. `### Nicht Teil dieser Aufgabe`
5. `### Akzeptanztests` (in exactly one ```` ```python ```` block immediately below)
6. `### Fertig, wenn`
7. `### Merge-Modus` (`automatisch nach Review` as default; `manuell durch Stephan (Betrieb, Server, Zugangsdaten)` for governance files)
8. `### Kontext`

*Note on headings:* Section headings remain in German as loop automation (`worker_tick.py`, `acceptance_guard.py`, `scripts/issuecheck.py`) expects these exact headings.

### Step 5: Pre-Flight Check (`scripts/issuecheck.py`)
Save draft locally as a file (e.g. `entwuerfe/issue-<topic>.md`) and run verification:

```bash
python scripts/issuecheck.py entwuerfe/issue-<topic>.md
```

- Validates required headings, merge mode, and path specifications.
- Checks AST for forbidden imports (network, randomness), skip markers (`skip`/`xfail`), and absolute `/root` paths.
- Executes acceptance tests with pytest in isolation against the repository.
- **Expected result:** Before implementation, tests must fail (exit code != 0 / FAIL / `ImportError` / `AssertionError`).
- Only when `scripts/issuecheck.py` succeeds with `Akzeptanztests sind valide und bereit für die Sandbox` is the draft ready.

*Note for planners without shell access:* If the script cannot run due to lack of a shell, label the issue `entwurf` instead of `bereit` and add a note in context („Akzeptanztests noch nicht ausgeführt (Planer ohne Shell)“).

### Step 6: Present Draft in Chat
Present title, complete issue body, and labels to Stephan in chat:
„Soll ich das Issue so anlegen?“
Only create the issue after Stephan approves.

### Step 7: Create Issue
Create the issue via `gh issue create` with the following labels:
- `worker-task`
- exactly one `agent-<family>` (`agent-qwen`, `agent-antigravity`, `agent-claude`)
- `bereit` (or `entwurf` if unverified without shell)
- `blockiert` if dependent on other issues/PRs

Finally, report the issue number and link back to Stephan.

---

## Issue Body Template

````markdown
### Ziel

<One or two sentences: What should be different afterwards?>

### Betroffene Dateien

- `sandbox/<file>.py` (<function>)
- `tests/acceptance/test_issue_<N>.py`: Akzeptanztests unten, zeichengenau
- `tests/test_<topic>.py` (new): dedicated tests

### Schnittstellen

```python
def <function>(<param>: <type>) -> <return_type>:
    """<Behavior, error handling, edge cases>"""
```

### Nicht Teil dieser Aufgabe

- no changes to install.sh or systemd units
- no refactoring of unrelated files

### Akzeptanztests

```python
import pytest
from sandbox.<file> import <function>


def test_<property>():
    assert <function>(...) == ...
```

### Fertig, wenn

- [ ] Akzeptanztests grün
- [ ] Weitere Tests decken das neue Verhalten ab (Netzwerk nur über Fakes)
- [ ] `python -m pytest -q` komplett grün

### Merge-Modus

automatisch nach Review

### Kontext

- Motivation / ROADMAP milestone / bug report
- Pre-Flight: verified with `python scripts/issuecheck.py` (red before implementation: <reason>)
````
