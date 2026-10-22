# Working via MCP (Without Shell Access)

For agents operating without shell access: follow identical rules to [`AGENTS.md`](../AGENTS.md),
using GitHub MCP tools instead of the `gh` CLI.

## Befehle → MCP-Werkzeuge (Commands → MCP Tools)

Always set `owner: tripitest-art`; `repo` is the project under development—for this repository: `agent-sandbox-infra`.

| In `AGENTS.md` | MCP Tool |
|---|---|
| Read file | `get_file_contents` (`ref`: `main` or feature branch) |
| `gh issue view <N>` | `issue_read` (`method: get`, for comments `get_comments`) |
| `gh issue list …` | `search_issues`, e.g. `repo:tripitest-art/agent-sandbox-infra is:issue is:open label:worker-task label:bereit -label:blockiert`, sorted by `created` ascending |
| `gh issue edit --add-label/--remove-label` | `issue_write` (`method: update`, `labels`: **complete** updated list) |
| `gh issue comment` | `add_issue_comment` |
| `git checkout -b <branch>` | `create_branch` (`from_branch: main`) |
| `git commit` + `git push` | `push_files` (multiple files, single atomic commit) |
| `gh pr create` | `create_pull_request` (`base: main`, body per `.github/pull_request_template.md`, with `Closes #<N>`) |
| `gh pr edit` (body, title) | `update_pull_request` |
| `gh pr view --comments` | `pull_request_read` (`get`, `get_comments`, `get_review_comments`) |
| `gh pr diff` | `pull_request_read` (`get_diff`, for large PRs `get_files`) |
| `gh pr checks` | `pull_request_read` (`get_check_runs`) |
| `gh pr view --json reviews,commits` | `pull_request_read` (`get_reviews`, `get_commits`) |
| `gh pr review --comment` | `pull_request_review_write` (`method: create`, `event: COMMENT`, `body`) |
| `gh pr merge --squash` | `merge_pull_request` (`merge_method: squash`) — in this repo **never**: Stephan merges exclusively (Rule 4) |
| `gh pr list …` | `list_pull_requests` / `search_pull_requests` |
| CI log on red check | `get_job_logs` |

## Regeln, die nur über MCP gelten (Rules Specific to MCP)

- **`push_files` writes entire files:** Always send full file contents, retrieved beforehand via `get_file_contents` from the branch. Never use "…" or placeholder truncations: whatever is missing is deleted.
- **Verbatim acceptance tests:** Copy the pytest block from the issue without alterations. The first commit on the branch must contain strictly `tests/acceptance/test_issue_<N>.py`.
- **Labels:** `issue_write` overwrites the entire label set. Read all labels first with `issue_read`, swap only `bereit` for `in-arbeit`, and write back the full list. Never pass title or body unless explicitly editing them. Only Stephan may apply `freigabe-geschützt`.
- **Bottlenecks (Rule 8):** `sandbox/worker_tick.py`, `sandbox/review_tick.py`, and `sandbox/install.sh`. Check whether an open issue labeled `in-arbeit` already claims the same file before claiming.
- **Verify after every `push_files`:** Use `pull_request_read` (`get_files`) to verify diff stats. If a file shows more deletions than additions, it was likely truncated: restore the full content immediately.
- **Tests:** Without shell access, rely on CI check `tests`. A task is done only when `tests` and `acceptance-guard` are green. Any changes to `.github/`, `conftest.py`, or pytest configuration require `freigabe-geschützt` (Stephan).
- **Context window exhausted?** Stop and explain status via issue comment rather than guessing (Worker, Step 6).
