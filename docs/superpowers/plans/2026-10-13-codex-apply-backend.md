# JobHop Codex Application Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task after the text-backend plan. Steps use checkbox syntax for tracking.

**Goal:** Replace the inherited Claude browser agent with the signed-in Codex CLI while retaining ApplyPilot's Chrome, Playwright, job queue, dashboard, and result handling under the JobHop name.

**Architecture:** Reuse `run_codex_exec()` from the companion text-backend plan. Build one worker-scoped Codex invocation per job with only the Playwright and restricted Gmail MCP servers, parse Codex JSONL events into the inherited progress and result flow, and keep the browser worker's shell sandbox read-only.

**Tech Stack:** Python 3.11+, Typer, Codex CLI `codex exec --json`, Playwright MCP, Chrome DevTools Protocol, Rich dashboard.

**Spec:** [2026-10-13-codex-backend-design.md](../specs/2026-10-13-codex-backend-design.md)

**Dependency:** Complete [2026-10-13-codex-text-backend.md](2026-10-13-codex-text-backend.md) first; this plan consumes `CodexResult`, `run_codex_exec()`, and `codex_login_status()` from Task 2 of that plan.

## Global Constraints

- Use `--ignore-user-config` plus Codex's per-invocation config overrides to register the worker's Playwright MCP server at that worker's Chrome DevTools endpoint and the Gmail MCP server with the current restrictions on mail mutation.
- Do not edit the user's global Codex configuration; the desktop app and other CLI tasks must retain their settings.
- Run from the isolated JobHop worker directory and pass only the job prompt and files already used by the current workflow.
- Preserve Ctrl+C process-tree cleanup, timeouts, Chrome cleanup, and worker isolation. Make dry-run a read-only preview: open the target URL in a clean temporary Chrome profile before Codex starts, allowlist only browser inspection tools, disable shell/file/browser/computer-use/app/plugin/code-mode/subagent tools and Gmail, withhold raw resume/cover-letter content and identifiers, restore saved job status and timestamps, and do not submit a form, send email, or create an account. The first integration run will use `jobhop apply --dry-run --limit 1`.
- Keep Codex's built-in shell sandbox read-only for live work and disable its shell, native browser/computer-use, app/plugin, code-mode, and subagent tools during preview. Do not use Codex's dangerous approval-and-sandbox bypass option, and configure only the MCP servers and permissions needed by this workflow.
- Answer application questions from facts in the applicant's profile and resume. Do not claim skills, credentials, or personal history that those sources do not establish. Remove unsupported fixed answers from the prompt. When a required answer cannot be supported, stop and flag it for manual review rather than guessing.
- The shared Codex process adapter returns `CodexResult(final_text: str, usage: dict[str, int] | None, turns: int)` and calls `on_event` and `on_process` as defined by the text-backend plan.

## Review Focus

- Per-worker config includes that worker's own CDP port; concurrent workers must not control each other's browser. (Task 2)
- A timed-out/interrupted Codex process is removed from the process registry and its child process tree is terminated. (Task 2)
- MCP tool-call events update progress without being mistaken for the final `RESULT:*` message. (Task 2)
- Codex failures, malformed output, and a missing result marker become a failed job without marking it applied. (Task 2)
- The prompt does not guess screening answers or claim unverified skills, and dry-run cannot navigate from the opened job page, exposes only browser inspection tools, disables local shell/file/browser/computer-use/app/plugin/code-mode/subagent tools and Gmail, withholds raw applicant materials and identifiers, and restores database state. (Task 1 and Task 2)

---

### Task 1: Correct application-answer truthfulness and Codex instructions

**Files:**
- Modify: `src/applypilot/apply/prompt.py`

**Interfaces:**
- Keep `build_prompt(job: dict, tailored_resume: str, cover_letter: str | None = None, dry_run: bool = False) -> str` compatible with current callers.

- [ ] Update the prompt header and agent instructions to address Codex CLI with Playwright MCP.
- [ ] Remove the rule that says to answer "yes" to same-domain skills questions without evidence and remove fixed answers for age, background checks, felony history, and prior employment unless the profile supplies those facts. Require hard-fact and experience answers to be supported by `profile.json` or the tailored resume.
- [ ] Instruct the agent to stop and emit `RESULT:FAILED:manual_review_required` during a live run when a required question cannot be answered truthfully from those sources; during dry-run report the missing answer in the preview and finish with `RESULT:DRY_RUN`. Keep location, authorization, salary, blocked-site, and other result-marker rules intact.
- [ ] Keep dry-run read-only: open the target job before Codex starts; inspect it without browser navigation, activating controls, editing fields, uploading files, sending email, or creating an account. Allowlist only inspection tools, disable shell/file/browser/computer-use/app/plugin/code-mode/subagent tools and Gmail, withhold raw applicant materials and identifiers, restore saved application state, and emit `RESULT:DRY_RUN`.
- [ ] Commit the prompt safeguard as `fix: keep application answers grounded in applicant facts`.

### Task 2: Run each browser job through Codex with isolated MCP settings

**Files:**
- Modify: `src/applypilot/apply/launcher.py`
- Modify: `src/applypilot/apply/chrome.py` only if worker process cleanup needs a Codex-specific integration

**Interfaces:**
- `run_job(job: dict, port: int, worker_id: int = 0, model: str | None = None, dry_run: bool = False) -> tuple[str, int]` preserves the existing status/duration contract and adds `dry_run` as an outcome.
- The task consumes `run_codex_exec(prompt, model=..., cwd=..., config_overrides=..., timeout_s=300, on_event=..., on_process=...) -> CodexResult`.

- [ ] Replace `_claude_procs` with the Codex process registry while retaining the existing lock and Ctrl+C behavior through the adapter's `on_process` callback.
- [ ] Build per-invocation TOML overrides for the worker's Playwright MCP server (`npx -y @playwright/mcp@latest`, worker CDP endpoint, configured viewport) and Gmail MCP server. Apply the existing Gmail disabled-tool list using Codex's per-server `disabled_tools`; do not write or edit `%USERPROFILE%\\.codex\\config.toml`.
- [ ] Call `run_codex_exec()` from the isolated worker directory with the model override, 300-second timeout, read-only shell sandbox, and the worker's MCP overrides. Set Codex's per-server tool approval mode to `auto` only for the scoped Playwright/Gmail servers, while keeping the listed Gmail tools disabled.
- [ ] Map completed `mcp_tool_call` events' server/tool/arguments to dashboard action labels; append final `CodexResult.final_text` to the existing job log and classify it with the `RESULT:*` contract, including `RESULT:DRY_RUN`.
- [ ] Select `apply_status` before marking the job `in_progress`; let `release_lock(url, restore_status=None)` restore that previous value. On every dry-run exit path (success, failure, skip, exception, or interrupt), restore the previous status, log the preview/outcome, and never call `mark_result()`; count one processed job so `--limit 1` exits normally. Preserve ordinary applied/failed database transitions for live runs.
- [ ] Preserve locks, job release on cancellation, permanent-failure classification, Chrome cleanup, and cleanup on timeout/exception. Add `manual_review_required` to permanent failures so the job is not retried with the same unsupported answer. Rename Claude-specific worker/job logs to Codex-neutral names.
- [ ] Commit the worker change as `feat: run ApplyPilot browser workers with Codex`.

### Task 3: Report Codex usage without invented monetary costs

**Files:**
- Modify: `src/applypilot/apply/dashboard.py`
- Modify: `src/applypilot/apply/launcher.py`

**Interfaces:**
- Keep dashboard rendering and `get_totals()` caller-facing keys stable where practical; the `cost` value may be `None` when the provider does not publish monetary cost.

- [ ] Store token/turn usage when present in `turn.completed.usage`.
- [ ] Add per-worker input-token, output-token, and turn counters and render them in the dashboard.
- [ ] Represent unavailable monetary cost as `None` rather than `0.0`; render it as `N/A` in worker, total, and final summary output.
- [ ] Leave the API text-provider implementation unchanged; the apply worker always uses Codex and has no provider-supplied monetary-cost value.
- [ ] Commit usage presentation as `fix: show Codex usage without false cost totals`.

### Task 4: Update apply CLI and manual prompt launch

**Files:**
- Modify: `src/applypilot/cli.py` (apply command and `--gen` output)
- Modify: `src/applypilot/apply/launcher.py` (`main()` model signature and prompt config output)
- Modify: `README.md` (auto-apply invocation and dry-run guidance)

**Interfaces:**
- Apply's `--model` is optional; absent a CLI value, use `CODEX_MODEL` or the Codex CLI default.
- `--gen` prints a Codex CLI command that uses the generated prompt and per-worker MCP overrides without requiring changes to global Codex config.

- [ ] Change Claude-specific `--model` help/default and tier text to Codex model selection; propagate `None` when no override was supplied.
- [ ] Change the `--gen` manual launch instructions from Claude flags/JSON config to `codex exec --json --ephemeral --ignore-user-config` and the equivalent worker-scoped Codex MCP overrides.
- [ ] Update README auto-apply documentation to show `jobhop apply --dry-run --limit 1` as the first integration run and state that read-only preview makes no application submission, email, account, or database status change.
- [ ] Commit CLI/docs as `docs: document Codex auto-apply workflow`.

### Task 5: Perform the approved dry-run integration when prerequisites exist

**Files:**
- No source files; use existing local configuration only.

- [ ] Confirm the existing shared profile, resume, tailored job, Chrome, npx, and signed-in Codex CLI are already configured. Do not invent or overwrite applicant data to satisfy a missing prerequisite.
- [ ] If the prerequisites exist, run `jobhop apply --dry-run --limit 1` against one selected job and confirm there is no form submission, application email, account creation, or database status change. Do not submit a real application during this step.
- [ ] If personal profile/resume or tailored job data is missing, leave this step pending for the user's setup and report the exact missing prerequisite; do not start a live apply run.
