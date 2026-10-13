# ApplyPilot Codex Backend Design

**Date:** 2026-10-13
**Status:** Approved
**Source:** ApplyPilot 0.3.0 (`Pickle-Pixel/ApplyPilot`, AGPL-3.0-only)

## Goal

Create a separate local ApplyPilot source copy that uses the signed-in Codex CLI for every AI-backed stage and for browser-driven applications. Keep ApplyPilot's job sources, enrichment, scoring, tailoring, cover letters, profile, generated files, database, dashboard, and application workflow.

The Codex backend is the default and requires no model API key. Existing Gemini, OpenAI, and OpenAI-compatible local providers remain available only when the user explicitly selects the API backend. Do not silently switch from Codex to an API provider because an API key happens to exist.

## Current integration points

- `applypilot.llm.get_client()` is the shared synchronous text-generation entry point used by discovery extraction, enrichment, scoring, resume tailoring, and cover-letter generation.
- `config.get_tier()` and `check_tier()` currently gate AI stages on API keys and auto-apply on Claude Code plus Chrome.
- `cli.doctor()` reports API-key and Claude requirements.
- `applypilot.apply.launcher.run_job()` starts one Claude process per browser worker, feeds it a generated prompt, parses streamed JSON events, updates the dashboard, and reads a `RESULT:*` marker to update the database.
- Each apply worker gets a Chrome DevTools port and a per-worker MCP configuration for Playwright and Gmail. The apply prompt has a dry-run mode.
- The current apply prompt also instructs the agent to answer "yes" to some same-domain skills questions regardless of whether the applicant's profile or resume confirms that experience. This conflicts with the approved truthful-answer safeguard and must be corrected in the Codex prompt.
- User-specific state lives under `%USERPROFILE%\\.applypilot` by default (`APPLYPILOT_DIR` can override it), independently of the Python source installation.

## Design

### 1. Shared text-generation backend

Keep the existing `chat(messages, temperature, max_tokens)`, `ask(prompt, ...)`, and `close()` interface so callers do not need to know which provider is active. Refactor provider selection behind that interface:

- `APPLYPILOT_AI_PROVIDER=codex` is the default. Invoke the installed Codex executable with `codex exec --json --ephemeral --ignore-user-config --sandbox read-only --skip-git-repo-check`, send the assembled messages through stdin, and return only the final assistant response. Parse JSONL events rather than treating event/status output as model text. Use a configured `CODEX_MODEL` when present; otherwise let Codex use its CLI default model. Authentication remains in the existing Codex home directory.
- Convert system, user, and assistant messages to an explicit prompt transcript while preserving their order and role boundaries. Each call is stateless, matching current API calls; do not resume a previous Codex conversation.
- Codex CLI does not expose per-call temperature or token-limit controls. Keep those parameters in the shared interface for API providers, but document that Codex mode does not guarantee equivalent sampling or a hard output-token cap; existing prompt instructions and caller-side validation remain in force.
- Report a clear error when Codex is missing, not signed in, times out, exits unsuccessfully, emits malformed events, or provides no final response. Do not fall back to another provider automatically.
- `APPLYPILOT_AI_PROVIDER=api` selects the current Gemini/OpenAI/local-compatible implementation and its existing environment variables. Keep its current provider behavior behind the same interface.
- Do not pass API credentials to Codex. With the default backend, existing API keys in `%USERPROFILE%\\.applypilot\\.env` are ignored for provider selection.

Callers continue to validate and parse the returned text as they do today. Codex JSONL metadata and progress events stay out of the generated text returned to scoring, extraction, tailoring, and letter generation.

### 2. Browser application backend

Replace the Claude-specific worker process with a Codex CLI worker while retaining the existing job acquisition, Chrome lifecycle, per-worker ports, prompts, result markers, logs, retries, dashboard, and database transitions.

- Start one `codex exec --json --ephemeral` process per application job and stream its JSONL events to the existing worker dashboard and log.
- Use `--ignore-user-config` plus Codex's per-invocation config overrides to register the worker's Playwright MCP server at that worker's Chrome DevTools endpoint and the Gmail MCP server with the current restrictions on mail mutation. Do not edit the user's global Codex configuration; the desktop app and other CLI tasks must retain their settings.
- Use the configured Codex model or an explicit ApplyPilot model override consistently for that worker. Run from the isolated ApplyPilot worker directory and pass only the job prompt and files already used by the current workflow.
- Translate completed Codex tool-call events into dashboard action descriptions. Extract final assistant text from the documented message event and retain the existing `RESULT:APPLIED`, `RESULT:EXPIRED`, `RESULT:CAPTCHA`, `RESULT:LOGIN_ISSUE`, `RESULT:FAILED` contract.
- Preserve Ctrl+C process-tree cleanup, timeouts, Chrome cleanup, worker isolation, and dry-run prompt behavior. The first integration run will use `applypilot apply --dry-run` so it fills forms without submitting.
- Answer application questions from facts in the applicant's profile and resume. Do not claim skills, credentials, or experience that those sources do not establish. When a required answer cannot be supported, stop and flag it for manual review rather than guessing.
- Keep Codex's built-in shell sandbox read-only, do not use its dangerous approval-and-sandbox bypass option, and configure only the MCP servers and permissions needed by this workflow. This leaves unrelated user-configured MCP servers unavailable to the job agent.
- Claude-specific cost values are not available from Codex events. Show Codex token/turn usage when available; show monetary cost as unavailable instead of reporting a fabricated `$0.00`.

### 3. Setup, CLI, and user data

- Keep this source copy and its virtual environment separate from the earlier pip installation. Both installations use the same `%USERPROFILE%\\.applypilot` data directory unless `APPLYPILOT_DIR` is set.
- Update `applypilot doctor` and tier checks to report Codex CLI presence, `codex login status`, Chrome, and Node/npx. AI features require a working signed-in Codex CLI by default; they do not require an API key. API mode reports the selected API provider's requirements. Auto-apply also requires Chrome and Node/npx for Playwright MCP.
- Update `applypilot init` and CLI help so they describe Codex as the default backend, expose the backend/model selection, and do not ask for an API key in Codex mode.
- Keep `applypilot apply --dry-run`, explicit API-provider selection, profile/resume setup, manual ATS rules, blocked-site rules, and all existing application safeguards.
- Keep the upstream AGPL-3.0-only license and document that this is a modified local source copy.

## Data flow

1. Discovery/enrichment/scoring/tailoring/cover-letter callers send their existing messages through `get_client()`.
2. The Codex text adapter starts an ephemeral `codex exec` process using the user's existing Codex CLI sign-in, then returns the final response text to the caller.
3. The apply worker launches Chrome, starts a worker-scoped Playwright MCP connection through Codex CLI config overrides, and feeds the existing application prompt to Codex.
4. Codex JSONL events feed the worker dashboard/log. The final `RESULT:*` marker continues to drive ApplyPilot's existing database status update.

## References

- [Codex CLI non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) — `codex exec`, JSONL output, ephemeral runs, config overrides, and login status.
- [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) — CLI MCP server configuration and per-server tool allow/deny lists.

## Scope boundaries

- No rewrite of discovery sources, data model, database, dashboard, resume formats, scoring criteria, or ATS-specific behavior.
- No automatic provider switching, changes to global Codex settings, or API-key transfer to Codex.
- No submission of real job applications during setup or integration verification; the first run is dry-run.
- No unsupported claims in application answers; profile/resume facts govern responses.
- No changes to the installed ApplyPilot virtual environment as part of building this separate copy.

## Completion criteria

- Codex mode is the default for every call routed through `get_client()` and for application workers.
- The existing API providers work only when explicitly selected.
- Setup and diagnostics explain whether Codex is installed and signed in, and name any missing browser-stage dependency.
- Application progress, logs, result classification, worker cleanup, and database updates continue through the existing ApplyPilot workflow.
- A first manual integration run can use dry-run to fill forms without submitting an application.
