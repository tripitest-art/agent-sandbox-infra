# Planer-Ablauf und Agenten-Labels (Planner Workflow and Agent Labels)

For every planner agent, regardless of model family. The operating agreement is specified in [`AGENTS.md`](../AGENTS.md)
(Worker Tasks → Planner); this guide explains how to craft an issue that an implementation model can execute without ambiguities.
This document is tailored for this repository—all examples and test helpers originate directly here.

The Planner writes issues, never code: no branches, no commits, no PRs, no merges.

## Planer-Ablauf (Planner Workflow)

0. **Backlog:** Ideas without acceptance tests reside as issues without the label `bereit` (`ROADMAP.md`).
   The planner only converts them into a `worker-task` once open domain questions are answered.
1. **Clarify task:** From discussion with Stephan or from `ROADMAP.md`. Exactly one topic per issue;
   if larger than a single PR, split into manageable slices („Part 1 of #58“). Clarify open domain
   questions (thresholds, error-handling behavior) with Stephan; never guess.
2. **Check status:** Search open issues and PRs for duplicates and file overlaps (Rule 8).
   Overlaps → label `blockiert` and a line `Wartet auf: #X` (see `AGENTS.md`).
   Inspect existing acceptance tests that cover modified functions (`git grep <function> tests/acceptance`).
   Workers must not alter existing acceptance tests; the task must be designed so existing tests remain green.
   **Caution:** If any line in the issue body contains `Wartet auf:` followed by a number (even inside an
   example or acceptance test block), the review loop treats it as an active blocking dependency (#46)—preventing
   automatic merge.
3. **Inspect code:** Read every file mentioned in the issue on latest `main`. Reuse existing helpers and fakes
   rather than prescribing new ones—such as `FakeGH` in [`tests/test_worker_tick.py`](../tests/test_worker_tick.py)
   and the `_load()` pattern in [`tests/test_acceptance_guard.py`](../tests/test_acceptance_guard.py) to load loop scripts
   by file path. For the project registry, reference `tests/test_projects.py`.
4. **Draft issue text** using the exact sections produced by the issue template:
   `### Ziel`, `### Betroffene Dateien`, `### Schnittstellen`, `### Nicht Teil dieser Aufgabe`,
   `### Akzeptanztests`, `### Fertig, wenn`, `### Merge-Modus`, and `### Kontext`, in that exact order.
   - **Betroffene Dateien (Affected Files):** Exhaustive list, including function and location. Anything omitted
     must not be touched by the worker. Always include `tests/acceptance/test_issue_<N>.py` and a dedicated test file.
     Existing tests whose assertions intentionally change must be explicitly cited with file and line.
   - **Schnittstellen (Interfaces):** Exact signatures with types, return values, behavior on empty/`None`/error cases,
     and verbatim log messages if tests assert them. Written in exactly one ```` ```python ```` block.
   - **Nicht Teil dieser Aufgabe (Out of Scope):** Explicitly rule out adjacent "drive-by" refactoring.
   - **Merge-Modus (Merge Mode):** `automatisch nach Review` is the default: once approved by GLM, the review loop
     merges automatically. Stephan only reviews if GLM flags defects. `manuell durch Stephan (Betrieb, Server, Zugangsdaten)`
     is required whenever a governance file is modified (`manual_paths` in project registry: `AGENTS.md`, `.github/*`,
     `tests/acceptance/*`, `skills/*`, `docs/main-schutz.ruleset.json`) or Stephan requests manual review.
5. **Akzeptanztests (Acceptance Tests)** in **exactly one** ```` ```python ```` block directly under
   `### Akzeptanztests` (this is how `acceptance-guard` parses it). Tests must:
   - verify strictly the public interface from the issue, not internal implementation details;
   - run offline without network, `gh`, or server dependencies (scripts loaded by file path as in `tests/test_worker_tick.py`),
     without real clock or randomness, and without `skip`/`xfail`;
   - import only standard library modules and this repository's scripts;
   - write only into `tmp_path`. **Never** write to `/root` (non-writable on developer machines and disallowed in CI, #39).
6. **Verify acceptance tests (with shell):** Save draft locally (e.g. `entwuerfe/issue-<topic>.md`) and run
   `python scripts/issuecheck.py <file>`. The script validates required headings, merge mode, AST constraints (no network,
   no time/randomness calls, no `skip`/`xfail`, no hardcoded `/root` paths, no accidental `Wartet auf:` lines), and executes
   the acceptance test in isolation with pytest. The test MUST be **red** (failing) prior to implementation,
   and it MUST stay green when the suite runs as root (the `pre-push` hook does): `issuecheck.py`
   runs the block in a second role and reports tests that are additionally red there as
   `laufnutzerabhaengig`.
   The skill `skills/agent-sandbox-planner/SKILL.md` guides this workflow. Record the pre-flight result under context.
   **Without shell access:** Apply label `entwurf` instead of `bereit` and state in context:
   „Akzeptanztests noch nicht ausgeführt (Planer ohne Shell)“. Stephan or an agent with shell runs `issuecheck.py` and
   updates `entwurf` → `bereit`.
   **Satisfiability check (before `bereit`):** A test that is red is not yet a test that *can* turn green. Before setting `bereit`,
   write a throwaway reference implementation in a temporary worktree (never committed) and run the acceptance block against it:
   it MUST pass. Reason: on 22/23 Oct 2026 four issues (#223, #224, #227, #264) reached the worker with unsatisfiable tests
   (unconditional `pytest.fail(...)` bodies, `pytest.raises(AssertionError)` around the code under test, an undefined `TEST_CMD`
   in a fake, one `gh` fake answering every query with the same list) and blocked `sandbox/worker_tick.py` for hours. Also reject:
   `lambda *a, **k: <list>` as a catch-all `gh` fake (separate the candidate list from the `in-arbeit` list and give candidates an
   `author` and a „Betroffene Dateien“ section, see `FakeGH` in `tests/test_worker_tick.py`), and names used in fakes that the
   test file does not define. Record the result under context as its own line, exactly in this spelling:

   Referenzumsetzung: Akzeptanztests grün
7. **Present draft:** Present title, body, and labels to Stephan in chat; only create the issue after his approval.
8. **Create issue** with labels `worker-task`, `phase-N`, `bereit` (or `entwurf` without shell), exactly one
   `agent-<family>` (see „Agenten-Labels“), and `blockiert` if dependent on other issues. Report issue number and link.
   Keep `test_issue_<N>` in the text; the worker replaces `<N>` with the actual issue number.

Planners may only modify an issue as long as it does not carry label `in-arbeit`. Once in progress, communicate via comments.
If an acceptance test must be corrected: update the issue; the subsequent PR will require `freigabe-geschützt` (`AGENTS.md`, Worker 5).

## Agenten-Labels (Agent Labels)

The planner assigns exactly one model family label to each worker issue. Workers pick up only issues carrying their label (`AGENTS.md`, Autonomous Selection). The Reviewer always belongs to a different family (Rule 6).

| Label | Worker | Matching Issues |
|---|---|---|
| `agent-qwen` | Sandbox Worker (OpenCode / Qwen Code; DeepSeek via LiteLLM) | Well-defined interface and acceptance tests, including bottleneck files from Rule 8 |
| `agent-antigravity` | Antigravity / Gemini | Medium: multiple files, architectural patterns, first escalation level for Qwen |
| `agent-claude` | Claude Code Opus + Human | Complex: operations, server configuration, final escalation tier |

When in doubt, choose the higher tier. An issue that fails three times under one agent family is escalated to the next tier (`agent-qwen` → `agent-antigravity` → `agent-claude`); this is permitted as long as the issue is no longer `in-arbeit`.
