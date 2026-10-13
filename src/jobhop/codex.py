"""Small, non-interactive adapter for the signed-in Codex CLI."""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

log = logging.getLogger(__name__)


class CodexExecutionError(RuntimeError):
    """Raised when a Codex CLI invocation cannot produce a final answer."""


@dataclass(frozen=True)
class CodexResult:
    """Final agent response and usage reported by Codex, when available."""

    final_text: str
    usage: dict[str, int] | None
    turns: int


_MODEL_CREDENTIAL_ENV_NAMES = {
    "ANTHROPIC_API_KEY",
    "AWS_BEARER_TOKEN_BEDROCK",
    "CODEX_API_KEY",
    "COHERE_API_KEY",
    "CEREBRAS_API_KEY",
    "DEEPSEEK_API_KEY",
    "FIREWORKS_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "HUGGINGFACEHUB_API_TOKEN",
    "HF_TOKEN",
    "LLM_API_KEY",
    "LLM_API_TOKEN",
    "MISTRAL_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "PERPLEXITY_API_KEY",
    "REPLICATE_API_TOKEN",
    "TOGETHER_API_KEY",
    "XAI_API_KEY",
}
_SECRET_PATTERN = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"github_pat_[A-Za-z0-9_]{12,}|Bearer\s+[A-Za-z0-9._~+/-]{12,})"
)
_STDERR_LIMIT = 6000


def _redact_sensitive(text: str) -> str:
    return _SECRET_PATTERN.sub("[redacted]", text)


def find_codex_cli() -> str | None:
    """Return the Codex executable available on PATH, if any."""
    return shutil.which("codex") or shutil.which("codex.exe")


def codex_login_status(executable: str | None = None) -> tuple[bool, str]:
    """Return login state and a concise, credential-free diagnostic."""
    cli = executable or find_codex_cli()
    if not cli:
        return False, "Codex CLI was not found on PATH."

    try:
        result = subprocess.run(
            [cli, "login", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "Codex login status check timed out."
    except OSError as exc:
        return False, f"Could not run Codex login status ({exc.__class__.__name__})."

    if result.returncode == 0:
        return True, "Signed in."
    return False, f"Not signed in or login status failed (exit code {result.returncode})."


def _child_environment() -> dict[str, str]:
    """Keep Codex CLI auth and PATH while stripping model-provider API keys."""
    env = os.environ.copy()
    for name in list(env):
        upper_name = name.upper()
        if upper_name in _MODEL_CREDENTIAL_ENV_NAMES:
            env.pop(name, None)
            continue
        # Cover provider-specific API keys and tokens without removing unrelated
        # service credentials such as CAPSOLVER_API_KEY.
        if upper_name.endswith(("_API_KEY", "_API_TOKEN")) and upper_name.split("_", 1)[0] in {
            "AZURE", "BEDROCK", "CLAUDE", "CODEX", "COHERE", "GEMINI", "GOOGLE",
            "GROQ", "LLAMA", "MISTRAL", "OPENAI", "OPENROUTER", "PERPLEXITY",
            "REPLICATE", "TOGETHER", "XAI",
        }:
            env.pop(name, None)
    return env


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Stop Codex and any MCP children spawned during this invocation."""
    if proc.poll() is not None:
        return

    if platform.system() == "Windows":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.terminate()
            except OSError:
                pass

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            log.warning("Codex process %s did not exit after kill", proc.pid)


def run_codex_exec(
    prompt: str,
    *,
    model: str | None = None,
    cwd: Path | None = None,
    config_overrides: Sequence[str] = (),
    timeout_s: int = 120,
    on_event: Callable[[dict], None] | None = None,
    on_process: Callable[[subprocess.Popen | None], None] | None = None,
) -> CodexResult:
    """Run one isolated, read-only Codex CLI turn and collect its JSONL events.

    The prompt is sent through stdin, avoiding command-line length limits and
    keeping its contents out of the process listing. Codex uses its saved
    authentication from ``CODEX_HOME`` (or the normal default) and does not
    receive provider API keys from the parent environment.
    """
    cli = find_codex_cli()
    if not cli:
        raise CodexExecutionError(
            "Codex CLI was not found on PATH. Install it and sign in with `codex login`."
        )

    command = [
        cli,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
    ]
    if model:
        command.extend(["--model", model])
    for override in config_overrides:
        command.extend(["--config", override])
    command.append("-")

    popen_kwargs: dict = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "env": _child_environment(),
        "cwd": str(cwd) if cwd else None,
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise CodexExecutionError("Codex CLI could not be started; check its PATH entry.") from exc
        raise CodexExecutionError(f"Could not start Codex CLI ({exc.__class__.__name__}).") from exc

    if on_process:
        try:
            on_process(proc)
        except Exception:
            _terminate_process_tree(proc)
            on_process(None)
            raise

    final_text = ""
    turn_count = 0
    usage_totals: dict[str, int] = {}
    usage_seen = False
    stream_errors: list[str] = []
    callback_errors: list[Exception] = []
    stderr_parts: list[str] = []
    stderr_size = 0
    stderr_lock = threading.Lock()

    def _read_stderr() -> None:
        nonlocal stderr_size
        assert proc.stderr is not None
        for chunk in iter(lambda: proc.stderr.read(1024), ""):
            with stderr_lock:
                stderr_parts.append(chunk)
                stderr_size += len(chunk)
                while stderr_size > _STDERR_LIMIT and stderr_parts:
                    removed = stderr_parts.pop(0)
                    stderr_size -= len(removed)

    def _read_stdout() -> None:
        nonlocal final_text, turn_count, usage_seen
        assert proc.stdout is not None
        for line_number, line in enumerate(proc.stdout, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                stream_errors.append(f"Malformed Codex JSONL at line {line_number}: {exc.msg}.")
                continue
            if not isinstance(event, dict):
                stream_errors.append(f"Codex JSONL line {line_number} was not an object.")
                continue

            event_type = event.get("type")
            if event_type == "turn.started":
                turn_count += 1
            elif event_type == "turn.completed":
                usage = event.get("usage")
                if isinstance(usage, dict):
                    usage_seen = True
                    for key, value in usage.items():
                        if isinstance(value, int) and not isinstance(value, bool):
                            usage_totals[key] = usage_totals.get(key, 0) + value
            elif event_type in {"error", "turn.failed"}:
                detail = event.get("message") or event.get("error") or event.get("reason")
                if isinstance(detail, dict):
                    detail = detail.get("message") or detail.get("detail")
                message = _redact_sensitive(str(detail))[:500] if detail else "Codex reported an execution failure."
                stream_errors.append(message)
            elif event_type == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str):
                        final_text = text

            if on_event:
                try:
                    on_event(event)
                except Exception as exc:
                    callback_errors.append(exc)

    stdout_thread = threading.Thread(target=_read_stdout, name="codex-stdout", daemon=True)
    stderr_thread = threading.Thread(target=_read_stderr, name="codex-stderr", daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False

    try:
        assert proc.stdin is not None
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            try:
                proc.stdin.close()
            except OSError:
                pass
            stream_errors.append(f"Could not send prompt to Codex ({exc.__class__.__name__}).")

        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_tree(proc)
        finally:
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

        if timed_out:
            raise CodexExecutionError(f"Codex did not finish within {timeout_s} seconds; its process tree was stopped.")

        with stderr_lock:
            stderr_text = _redact_sensitive("".join(stderr_parts)).strip()

        if stream_errors:
            detail = "; ".join(stream_errors[:3])
            raise CodexExecutionError(detail)
        if callback_errors:
            raise CodexExecutionError(
                f"A Codex progress callback failed ({callback_errors[0].__class__.__name__})."
            ) from callback_errors[0]
        if proc.returncode != 0:
            detail = f" Codex CLI: {stderr_text[-1200:]}" if stderr_text else ""
            raise CodexExecutionError(
                f"Codex exited with status {proc.returncode}.{detail}"
            )
        if not final_text.strip():
            detail = f" Codex CLI: {stderr_text[-1200:]}" if stderr_text else ""
            raise CodexExecutionError(f"Codex completed without a final agent message.{detail}")

        return CodexResult(
            final_text=final_text,
            usage=usage_totals if usage_seen else None,
            turns=turn_count,
        )
    finally:
        # A timeout may have stopped the parent while a spawned MCP child lives
        # on. The tree helper is safe to call after normal process completion.
        if proc.poll() is None:
            _terminate_process_tree(proc)
        if on_process:
            try:
                on_process(None)
            except Exception:
                log.debug("Codex process cleanup callback failed", exc_info=True)
