# ApplyPilot Codex Text Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Route ApplyPilot's text-based AI work through the signed-in Codex CLI by default while keeping API providers available through explicit selection.

**Architecture:** Add one reusable Codex CLI process/event adapter, then connect it to ApplyPilot's existing synchronous LLM interface. Update tier checks, the setup wizard, diagnostics, and documentation to reflect the Codex default. The browser application worker is described in the companion plan and consumes the same Codex process adapter.

**Tech Stack:** Python 3.11+, Typer, subprocess, Codex CLI `codex exec --json`, existing httpx provider implementation.

**Spec:** [2026-10-13-codex-backend-design.md](../specs/2026-10-13-codex-backend-design.md)

## Global Constraints

- `APPLYPILOT_AI_PROVIDER=codex` is the default.
- Existing Gemini, OpenAI, and OpenAI-compatible local providers remain available only when the user explicitly selects the API backend.
- Do not silently switch from Codex to an API provider because an API key happens to exist.
- Keep this source copy and its virtual environment separate from the earlier pip installation. Both installations use the same `%USERPROFILE%\\.applypilot` data directory unless `APPLYPILOT_DIR` is set.
- Keep the upstream AGPL-3.0-only license and document that this is a modified local source copy.
- No automatic provider switching, changes to global Codex settings, or API-key transfer to Codex.

## Review Focus

- Missing Codex executable: report the dependency clearly and never choose an API provider implicitly. (Tasks 2 and 4)
- Expired or missing Codex sign-in: report the login failure separately from a missing executable. (Tasks 2 and 4)
- API keys present in `.env` with no provider setting: continue to select Codex. (Tasks 3 and 5)
- JSONL with malformed events, a failed turn, nonzero process exit, timeout, or no final agent message: return a useful error, never event JSON as generated text. (Tasks 2 and 3)
- Existing callers pass temperature and token limits: preserve the shared method signatures and document Codex's lack of equivalent per-call controls. (Task 3)

---

### Task 1: Prepare the separate local Python environment

**Files:**
- Modify: `.gitignore`
- Create: `.venv/` (ignored local environment)

- [ ] Add `.venv/` to `.gitignore` so the environment and installed packages cannot enter source commits.
- [ ] Create `.venv` inside `ApplyPilot-Codex`, install this checkout with `python -m pip install -e .`, then install `python-jobspy` and the runtime dependencies listed in the upstream README.
- [ ] Confirm all commands use `ApplyPilot-Codex\\.venv`; do not reuse or alter the earlier parent-directory `.venv`.
- [ ] Commit only `.gitignore` as `chore: ignore project-local virtual environment`.

### Task 2: Add the reusable Codex CLI process adapter

**Files:**
- Create: `src/applypilot/codex.py`

**Interfaces:**
- Produces `CodexResult(final_text: str, usage: dict[str, int] | None, turns: int)`.
- Produces `find_codex_cli() -> str | None` and `codex_login_status(executable: str | None = None) -> tuple[bool, str]`.
- Produces `run_codex_exec(prompt: str, *, model: str | None = None, cwd: Path | None = None, config_overrides: Sequence[str] = (), timeout_s: int = 120, on_event: Callable[[dict], None] | None = None, on_process: Callable[[Popen | None], None] | None = None) -> CodexResult`.
- `run_codex_exec` always uses `exec --json --ephemeral --ignore-user-config --sandbox read-only --skip-git-repo-check -`; it sends the prompt through stdin and passes each config override as a separate `--config` argument.

- [ ] Create the result/error types, Codex CLI lookup, and login-status helper in `src/applypilot/codex.py`. The login helper uses `codex login status`, returns its exit status and concise output, and never prints credentials.
- [ ] Implement `run_codex_exec` with argument-list process creation, UTF-8 JSONL stdout parsing, concurrently drained stderr, the optional model flag, and the exact flags defined in the interface. Remove `GEMINI_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY`, `LLM_API_KEY`, and other model API credentials from the child environment while retaining Codex home/auth and `PATH`.
- [ ] Extract the final text from the last completed `agent_message` item, accumulate token counts from `turn.completed.usage`, and count `turn.started` events. Forward each parsed event to `on_event`; reject malformed JSON, `error` and `turn.failed` events, nonzero exit, empty final text, and timeouts with `CodexExecutionError`.
- [ ] Notify `on_process` immediately after spawn and again with `None` at exit so the apply worker can preserve Ctrl+C process-tree cleanup. Ensure timeout cleanup also terminates the process tree.
- [ ] Commit the adapter as `feat: add reusable Codex CLI execution adapter`.

### Task 3: Route the shared LLM interface through Codex by default

**Files:**
- Modify: `src/applypilot/llm.py`

**Interfaces:**
- Keep `get_client()`, `chat(messages, temperature=0.0, max_tokens=4096)`, `ask(prompt, **kwargs)`, and `close()` usable by all existing callers.
- Add a Codex-backed client that calls `run_codex_exec`; retain the current `LLMClient` API implementation for explicit API mode.

- [ ] Change provider selection to read `APPLYPILOT_AI_PROVIDER`, defaulting to `codex`; accept only `codex` and `api`, and explain invalid values.
- [ ] Assemble `system`, `user`, and `assistant` messages into a role-labeled transcript in their original order; use `CODEX_MODEL` only when set and return only the adapter's `final_text`.
- [ ] Keep temperature and `max_tokens` accepted for compatibility. In Codex mode, do not claim they are enforced; leave prompt instructions and existing caller validation in charge of output shape.
- [ ] Keep `_detect_provider()` and the existing Gemini/OpenAI/local retry behavior behind explicit `APPLYPILOT_AI_PROVIDER=api` selection. API keys must not override the Codex default.
- [ ] Remove `GEMINI_API_KEY`, `OPENAI_API_KEY`, `LLM_API_KEY`, and other API credential variables from the child Codex process environment while preserving the user's Codex home and normal executable lookup.
- [ ] Commit the LLM routing change as `feat: use Codex for ApplyPilot text generation`.

### Task 4: Make tier checks and diagnostics Codex-aware

**Files:**
- Modify: `src/applypilot/config.py`
- Modify: `src/applypilot/cli.py` (doctor and tier summary only)

**Interfaces:**
- `get_tier()` continues returning tiers 1–3. Tier 2 checks the selected text backend; Tier 3 adds Codex and browser prerequisites.
- `check_tier(required: int, feature: str) -> None` reports missing components for the selected backend.

- [ ] Update `get_tier()` so Tier 2 checks the selected text backend; Tier 3 always requires Codex CLI/sign-in, Chrome, and Node/npx even when the text backend is explicitly set to API.
- [ ] Update `check_tier()` messages so Tier 2 never asks for an API key in Codex mode and Tier 3 names missing Codex, Chrome, or Node/npx requirements.
- [ ] Update `applypilot doctor` to show selected backend/model, Codex CLI path, Codex sign-in status, API mode requirements, Chrome, and npx without exposing any secret values.
- [ ] Update the doctor tier summary strings to describe Codex-based scoring/tailoring and auto-apply.
- [ ] Commit diagnostics as `feat: diagnose Codex ApplyPilot requirements`.

### Task 5: Update first-run setup and text-backend documentation

**Files:**
- Modify: `src/applypilot/wizard/init.py`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- The wizard stores `APPLYPILOT_AI_PROVIDER=codex` by default and accepts optional `CODEX_MODEL` without requesting an API key.
- API provider selection remains explicit and keeps existing API environment variable names.

- [ ] Change `_setup_ai_features()` to present Codex as the default choice and API providers as explicit alternatives; when Codex is selected, do not ask for or write a model API key. Update backend-specific `.env` settings without erasing CapSolver, proxy, or unrelated existing entries.
- [ ] Change `_setup_auto_apply()` and the wizard tier hints from Claude to Codex CLI, Chrome, and Node/npx.
- [ ] Update `.env.example` with `APPLYPILOT_AI_PROVIDER=codex`, optional `CODEX_MODEL`, explicit API-mode examples, and the existing CapSolver/Proxy entries.
- [ ] Update README setup, feature-stage descriptions, requirements, and environment variables to match the approved design and AGPL-3.0-only local fork.
- [ ] Commit setup/docs as `docs: describe Codex as ApplyPilot's default AI backend`.
