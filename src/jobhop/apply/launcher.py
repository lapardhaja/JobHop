"""Apply orchestration: acquire jobs, run Codex browser sessions, track results.

This is the main entry point for the apply pipeline. It pulls jobs from
the database, launches Chrome + Codex for each one, parses the
result, and updates the database. Supports parallel workers via --workers.
"""

import atexit
import json
import logging
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from rich.console import Console
from rich.live import Live

from jobhop import config
from jobhop.codex import CodexExecutionError, CodexResult, run_codex_exec
from jobhop.compensation import assess_salary, get_salary_floor
from jobhop.database import get_connection
from jobhop.eligibility import assess_location, is_student_role
from jobhop.apply import chrome, dashboard, prompt as prompt_mod
from jobhop.apply.chrome import (
    launch_chrome, cleanup_worker, kill_all_chrome,
    reset_worker_dir, cleanup_on_exit, _kill_process_tree,
    BASE_CDP_PORT,
)
from jobhop.apply.dashboard import (
    init_worker, update_state, add_event, get_state,
    render_full, get_totals,
)

logger = logging.getLogger(__name__)

# Blocked sites loaded from config/sites.yaml
def _load_blocked():
    from jobhop.config import load_blocked_sites
    return load_blocked_sites()

# How often to poll the DB when the queue is empty (seconds)
POLL_INTERVAL = config.DEFAULTS["poll_interval"]

# Thread-safe shutdown coordination
_stop_event = threading.Event()

# Track active Codex processes for skip (Ctrl+C) handling.
_codex_procs: dict[int, subprocess.Popen] = {}
_codex_lock = threading.Lock()
_skip_workers: set[int] = set()

# Register cleanup on exit
atexit.register(cleanup_on_exit)
if platform.system() != "Windows":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


# ---------------------------------------------------------------------------
# MCP config
# ---------------------------------------------------------------------------

def _codex_mcp_overrides(cdp_port: int, *, dry_run: bool = False) -> list[str]:
    """Build invocation-local MCP settings; never write the user's Codex config."""
    playwright_args = [
        "-y",
        "@playwright/mcp@latest",
        f"--cdp-endpoint=http://localhost:{cdp_port}",
        f"--viewport-size={config.DEFAULTS['viewport']}",
    ]
    gmail_args = ["-y", "@gongrzhe/server-gmail-autoauth-mcp"]
    # Retain the upstream Gmail MCP's mail-mutation restrictions. Read/search
    # tools remain available for verification flows; dry-run prompts forbid sends.
    gmail_tools = [
        "draft_email", "modify_email", "delete_email", "download_attachment",
        "batch_modify_emails", "batch_delete_emails", "create_label",
        "update_label", "delete_label", "get_or_create_label",
        "list_email_labels", "create_filter", "list_filters", "get_filter",
        "delete_filter",
    ]
    preview_tools = [
        "browser_snapshot", "browser_find",
        "browser_take_screenshot", "browser_console_messages",
        "browser_network_requests", "browser_wait_for",
    ]
    overrides = [
        'mcp_servers.playwright.command="npx"',
        f"mcp_servers.playwright.args={json.dumps(playwright_args)}",
        'mcp_servers.playwright.default_tools_approval_mode="auto"',
        'mcp_servers.gmail.command="npx"',
        f"mcp_servers.gmail.args={json.dumps(gmail_args)}",
        f"mcp_servers.gmail.disabled_tools={json.dumps(gmail_tools)}",
        'mcp_servers.gmail.default_tools_approval_mode="auto"',
    ]
    if dry_run:
        overrides.extend([
            f"mcp_servers.playwright.enabled_tools={json.dumps(preview_tools)}",
            "mcp_servers.gmail.enabled=false",
            # A hostile page must not be able to ask Codex to inspect local
            # applicant files or open another browser. The preview only needs
            # the explicitly allowlisted Playwright inspection tools.
            "features.shell_tool=false",
            "features.multi_agent=false",
            "features.browser_use=false",
            "features.browser_use_external=false",
            "features.computer_use=false",
            "features.apps=false",
            "features.plugins=false",
            "features.remote_plugin=false",
            # MCP browser tools are hosted through Codex's code-mode host.
            # Keep the host enabled while shell and unrelated integrations stay off.
            "features.code_mode_host=true",
        ])
    return overrides


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

_TRACKING_QUERY_KEYS = {
    "campaign", "campaignid", "gh_src", "lever-source", "ref", "ref_id",
    "referrer", "tracking", "trk",
}
_JOB_BOARD_SOURCE_VALUES = {
    "dice", "google", "indeed", "linkedin", "monster", "talent.com",
    "zip_recruiter", "zip-recruiter",
}


def canonical_application_destination(url: str | None, listing_url: str | None = None) -> str:
    """Normalize an application URL so tracking variants identify one destination."""
    if not url or not url.strip():
        return ""

    try:
        parts = urlsplit(urljoin(listing_url or "", url.strip()))
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""

    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        return ""
    netloc = host
    if port and not (
        (parts.scheme.lower() == "http" and port == 80)
        or (parts.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"

    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in _TRACKING_QUERY_KEYS
        and not (
            key.lower() in {"source", "src"}
            and value.strip().casefold() in _JOB_BOARD_SOURCE_VALUES
        )
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", netloc, path, urlencode(sorted(query)), parts.fragment))


def _destination_statuses(conn) -> tuple[dict[str, str], set[str]]:
    """Return applied destination keys and destinations currently being handled."""
    rows = conn.execute("""
        SELECT url, application_url, apply_status, applied_at
        FROM jobs
        WHERE applied_at IS NOT NULL
           OR apply_status IN ('applied', 'in_progress')
    """).fetchall()
    applied: dict[str, str] = {}
    in_progress: set[str] = set()
    for row in rows:
        destination = canonical_application_destination(
            row["application_url"] or row["url"], row["url"]
        )
        if not destination:
            continue
        if row["applied_at"] is not None or row["apply_status"] == "applied":
            applied.setdefault(destination, row["url"])
        elif row["apply_status"] == "in_progress":
            in_progress.add(destination)
    return applied, in_progress


def _mark_duplicate_destination_rows(conn, applied_url: str) -> int:
    """Record existing rows that share a completed application's destination."""
    source = conn.execute(
        "SELECT url, application_url FROM jobs WHERE url = ?", (applied_url,)
    ).fetchone()
    if not source:
        return 0
    destination = canonical_application_destination(
        source["application_url"] or source["url"], source["url"]
    )
    if not destination:
        return 0

    marked = 0
    siblings = conn.execute("""
        SELECT url, application_url, apply_status, applied_at
        FROM jobs
        WHERE url != ?
    """, (source["url"],)).fetchall()
    for sibling in siblings:
        if sibling["applied_at"] is not None or sibling["apply_status"] in {"applied", "in_progress"}:
            continue
        sibling_destination = canonical_application_destination(
            sibling["application_url"] or sibling["url"], sibling["url"]
        )
        if sibling_destination != destination:
            continue
        cursor = conn.execute("""
            UPDATE jobs
            SET apply_status = 'duplicate_applied', duplicate_of = ?,
                apply_error = NULL, agent_id = NULL
            WHERE url = ? AND applied_at IS NULL
              AND (apply_status IS NULL OR apply_status != 'in_progress')
        """, (source["url"], sibling["url"]))
        marked += cursor.rowcount
    return marked


def _record_duplicate_candidate(conn, candidate_url: str, applied_url: str) -> None:
    """Persist why a not-yet-applied posting is excluded from the queue."""
    conn.execute("""
        UPDATE jobs
        SET apply_status = 'duplicate_applied', duplicate_of = ?,
            apply_error = NULL, agent_id = NULL
        WHERE url = ? AND applied_at IS NULL
          AND (apply_status IS NULL OR apply_status IN ('failed', 'location_review', 'not_eligible_location'))
    """, (applied_url, candidate_url))


def _record_eligibility_status(conn, candidate_url: str, status: str) -> int:
    """Persist a non-application eligibility state for later review or retry."""
    cursor = conn.execute("""
        UPDATE jobs SET apply_status = ?, agent_id = NULL
        WHERE url = ? AND applied_at IS NULL
          AND (apply_status IS NULL OR apply_status IN ('failed', 'location_review', 'not_eligible_location'))
    """, (status, candidate_url))
    return cursor.rowcount


def acquire_job(target_url: str | None = None, min_score: int = 7,
                worker_id: int = 0, dry_run: bool = False) -> dict | None:
    """Atomically acquire the next job to apply to.

    Args:
        target_url: Apply to a specific URL instead of picking from queue.
        min_score: Minimum fit_score threshold.
        worker_id: Worker claiming this job (for tracking).

    Returns:
        Job dict or None if the queue is empty.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            salary_floor = get_salary_floor(config.load_profile())
        except FileNotFoundError:
            salary_floor = None
        applied_destinations, in_progress_destinations = _destination_statuses(conn)
        duplicate_updates = 0
        eligibility_updates = 0
        role_skipped = 0
        location_skipped = 0
        search_config = config.load_search_config()
        excluded_patterns = config.load_excluded_job_urls()
        exclusion_clause = ""
        exclusion_params: list[str] = []
        for pattern in excluded_patterns:
            exclusion_clause += " AND instr(lower(COALESCE(url, '')), ?) = 0"
            exclusion_clause += " AND instr(lower(COALESCE(application_url, '')), ?) = 0"
            exclusion_params.extend((pattern, pattern))

        if target_url:
            like = f"%{target_url.split('?')[0].rstrip('/')}%"
            rows = conn.execute(f"""
                SELECT url, title, site, application_url, tailored_resume_path,
                       salary,
                       fit_score, location, full_description, cover_letter_path,
                       apply_status, agent_id, last_attempted_at, applied_at
                FROM jobs
                WHERE (url = ? OR application_url = ? OR application_url LIKE ? OR url LIKE ?)
                  AND tailored_resume_path IS NOT NULL
                  AND applied_at IS NULL
                  AND (apply_status IS NULL OR apply_status IN ('failed', 'location_review', 'not_eligible_location'))
                  {exclusion_clause}
                ORDER BY fit_score DESC, discovered_at DESC
                LIMIT 1
            """, (target_url, target_url, like, like, *exclusion_params)).fetchall()
            row = None
            if rows:
                candidate = rows[0]
                can_select = True
                if is_student_role(candidate["title"]):
                    role_skipped += 1
                    can_select = False
                    if not dry_run:
                        eligibility_updates += _record_eligibility_status(
                            conn, candidate["url"], "not_eligible_role"
                        )
                else:
                    location = assess_location(
                        candidate["location"], candidate["full_description"], search_config
                    )
                    if location.status == "outside":
                        location_skipped += 1
                        can_select = False
                        if not dry_run:
                            eligibility_updates += _record_eligibility_status(
                                conn, candidate["url"], "not_eligible_location"
                            )
                    elif location.status == "unknown" and not dry_run:
                        location_skipped += 1
                        can_select = False
                        eligibility_updates += _record_eligibility_status(
                            conn, candidate["url"], "location_review"
                        )
                if can_select:
                    candidate = dict(candidate)
                    if location.status == "unknown":
                        candidate["_location_review"] = True
                    elif candidate.get("apply_status") in {"location_review", "not_eligible_location"}:
                        if not dry_run:
                            candidate["apply_status"] = None
                            conn.execute(
                                "UPDATE jobs SET apply_status = NULL WHERE url = ?",
                                (candidate["url"],),
                            )
                    destination = canonical_application_destination(
                        candidate["application_url"] or candidate["url"], candidate["url"]
                    )
                    if destination in applied_destinations:
                        if not dry_run:
                            _record_duplicate_candidate(
                                conn, candidate["url"], applied_destinations[destination]
                            )
                            duplicate_updates += 1
                    elif destination not in in_progress_destinations:
                        row = candidate
        else:
            blocked_sites, blocked_patterns = _load_blocked()
            # Build parameterized filters to avoid SQL injection
            params: list = [min_score]
            site_clause = ""
            if blocked_sites:
                placeholders = ",".join("?" * len(blocked_sites))
                site_clause = f"AND site NOT IN ({placeholders})"
                params.extend(blocked_sites)
            url_clauses = ""
            if blocked_patterns:
                url_clauses = " ".join(f"AND url NOT LIKE ?" for _ in blocked_patterns)
                params.extend(blocked_patterns)
            params.extend(exclusion_params)
            rows = conn.execute(f"""
                SELECT url, title, site, application_url, tailored_resume_path,
                       salary,
                       fit_score, location, full_description, cover_letter_path,
                       apply_status, agent_id, last_attempted_at, applied_at
                FROM jobs
                WHERE tailored_resume_path IS NOT NULL
                  AND applied_at IS NULL
                  AND (apply_status IS NULL OR apply_status IN ('failed', 'location_review', 'not_eligible_location'))
                  AND (apply_attempts IS NULL OR apply_attempts < ?)
                  AND fit_score >= ?
                  {site_clause}
                  {url_clauses}
                  {exclusion_clause}
                ORDER BY fit_score DESC, url
            """, [config.DEFAULTS["max_apply_attempts"]] + params).fetchall()
            # The fit score reflects role/profile fit. Pay is a separate
            # eligibility gate: known compensation below the profile floor
            # must never reach a browser worker.
            row = None
            salary_skipped = 0
            for candidate in rows:
                if is_student_role(candidate["title"]):
                    role_skipped += 1
                    if not dry_run:
                        eligibility_updates += _record_eligibility_status(
                            conn, candidate["url"], "not_eligible_role"
                        )
                    continue
                location = assess_location(
                    candidate["location"], candidate["full_description"], search_config
                )
                if location.status == "outside":
                    location_skipped += 1
                    if not dry_run:
                        eligibility_updates += _record_eligibility_status(
                            conn, candidate["url"], "not_eligible_location"
                        )
                    continue
                if location.status == "unknown" and not dry_run:
                    location_skipped += 1
                    eligibility_updates += _record_eligibility_status(
                        conn, candidate["url"], "location_review"
                    )
                    continue
                destination = canonical_application_destination(
                    candidate["application_url"] or candidate["url"], candidate["url"]
                )
                if destination in applied_destinations:
                    if not dry_run:
                        _record_duplicate_candidate(
                            conn, candidate["url"], applied_destinations[destination]
                        )
                        duplicate_updates += 1
                    continue
                if destination in in_progress_destinations:
                    continue
                assessment = assess_salary(candidate["salary"], salary_floor)
                if assessment.status == "below_floor":
                    salary_skipped += 1
                    continue
                if candidate["apply_status"] in {"location_review", "not_eligible_location"}:
                    candidate = dict(candidate)
                    if not dry_run:
                        candidate["apply_status"] = None
                        conn.execute(
                            "UPDATE jobs SET apply_status = NULL WHERE url = ?",
                            (candidate["url"],),
                        )
                if location.status == "unknown":
                    candidate = dict(candidate)
                    candidate["_location_review"] = True
                row = candidate
                break
            if salary_skipped:
                logger.info(
                    "Skipped %d job(s) below the saved salary floor while selecting an application.",
                    salary_skipped,
                )

        if role_skipped:
            logger.info("Skipped %d internship/co-op/apprenticeship posting(s).", role_skipped)
        if location_skipped:
            logger.info("Skipped %d posting(s) outside or missing the saved location preference.", location_skipped)
        if not row:
            if duplicate_updates or eligibility_updates:
                conn.commit()
            else:
                conn.rollback()
            return None

        job = dict(row)
        previous_status = row["apply_status"]
        salary_assessment = assess_salary(job.get("salary"), salary_floor)
        if salary_assessment.status == "below_floor":
            job["_salary_ineligible"] = True
            job["_salary_reason"] = salary_assessment.reason
            job["_salary_eligibility"] = salary_assessment.status
            job["_previous_apply_status"] = previous_status
            job["_previous_agent_id"] = row["agent_id"]
            job["_previous_last_attempted_at"] = row["last_attempted_at"]
            conn.rollback()
            return job

        job["_salary_eligibility"] = salary_assessment.status
        job["_salary_reason"] = salary_assessment.reason
        job["_previous_agent_id"] = row["agent_id"]
        job["_previous_last_attempted_at"] = row["last_attempted_at"]

        # Skip manual ATS sites (unsolvable CAPTCHAs). Preview mode must not
        # write even this queue annotation.
        from jobhop.config import is_manual_ats
        apply_url = row["application_url"] or row["url"]
        if is_manual_ats(apply_url):
            if dry_run:
                job["_manual_ats"] = True
                job["_previous_apply_status"] = previous_status
                conn.rollback()
                return job
            conn.execute(
                "UPDATE jobs SET apply_status = 'manual', apply_error = 'manual ATS' WHERE url = ?",
                (row["url"],),
            )
            conn.commit()
            logger.info("Skipping manual ATS: %s", row["url"][:80])
            return None

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE jobs SET apply_status = 'in_progress',
                           agent_id = ?,
                           last_attempted_at = ?
            WHERE url = ?
        """, (f"worker-{worker_id}", now, row["url"]))
        conn.commit()

        job["_previous_apply_status"] = previous_status
        return job
    except Exception:
        conn.rollback()
        raise


def mark_result(url: str, status: str, error: str | None = None,
                permanent: bool = False, duration_ms: int | None = None,
                task_id: str | None = None) -> None:
    """Update a job's apply status in the database."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    if status == "applied":
        conn.execute("""
            UPDATE jobs SET apply_status = 'applied', applied_at = ?,
                           apply_error = NULL, agent_id = NULL,
                           apply_duration_ms = ?, apply_task_id = ?
            WHERE url = ?
        """, (now, duration_ms, task_id, url))
        _mark_duplicate_destination_rows(conn, url)
    else:
        attempts = 99 if permanent else "COALESCE(apply_attempts, 0) + 1"
        conn.execute(f"""
            UPDATE jobs SET apply_status = ?, apply_error = ?,
                           apply_attempts = {attempts}, agent_id = NULL,
                           apply_duration_ms = ?, apply_task_id = ?
            WHERE url = ?
        """, (status, error or "unknown", duration_ms, task_id, url))
    conn.commit()


def release_lock(url: str, *, restore_previous: bool = False,
                 restore_status: str | None = None,
                 restore_agent_id: str | None = None,
                 restore_last_attempted_at: str | None = None) -> None:
    """Release a job lock, optionally restoring its exact prior state."""
    conn = get_connection()
    if restore_previous:
        conn.execute(
            "UPDATE jobs SET apply_status = ?, agent_id = ?, last_attempted_at = ? "
            "WHERE url = ? AND apply_status = 'in_progress'",
            (restore_status, restore_agent_id, restore_last_attempted_at, url),
        )
    else:
        conn.execute(
            "UPDATE jobs SET apply_status = NULL, agent_id = NULL WHERE url = ? AND apply_status = 'in_progress'",
            (url,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Utility modes (--gen, --mark-applied, --mark-failed, --reset-failed)
# ---------------------------------------------------------------------------

def gen_prompt(target_url: str, min_score: int = 7,
               model: str | None = None, worker_id: int = 0) -> Path | None:
    """Generate a prompt file for manual Codex CLI debugging.

    Returns:
        Path to the generated prompt file, or None if no job found.
    """
    job = acquire_job(target_url=target_url, min_score=min_score, worker_id=worker_id)
    if not job:
        return None
    if job.get("_salary_ineligible"):
        return None

    try:
        # Read resume text
        resume_path = job.get("tailored_resume_path")
        txt_path = Path(resume_path).with_suffix(".txt") if resume_path else None
        resume_text = ""
        if txt_path and txt_path.exists():
            resume_text = txt_path.read_text(encoding="utf-8")

        prompt = prompt_mod.build_prompt(job=job, tailored_resume=resume_text)
    finally:
        # Release the lock even when a resume or profile is malformed.
        release_lock(
            job["url"],
            restore_previous=True,
            restore_status=job.get("_previous_apply_status"),
            restore_agent_id=job.get("_previous_agent_id"),
            restore_last_attempted_at=job.get("_previous_last_attempted_at"),
        )

    # Write prompt file
    config.ensure_dirs()
    site_slug = (job.get("site") or "unknown")[:20].replace(" ", "_")
    prompt_file = config.LOG_DIR / f"prompt_{site_slug}_{job['title'][:30].replace(' ', '_')}.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    return prompt_file


def mark_job(url: str, status: str, reason: str | None = None) -> None:
    """Manually mark a job's apply status in the database.

    Args:
        url: Job URL to mark.
        status: Either 'applied' or 'failed'.
        reason: Failure reason (only for status='failed').
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    if status == "applied":
        conn.execute("""
            UPDATE jobs SET apply_status = 'applied', applied_at = ?,
                           apply_error = NULL, agent_id = NULL, duplicate_of = NULL
            WHERE url = ?
        """, (now, url))
        _mark_duplicate_destination_rows(conn, url)
    else:
        conn.execute("""
            UPDATE jobs SET apply_status = 'failed', apply_error = ?,
                           apply_attempts = 99, agent_id = NULL
            WHERE url = ?
        """, (reason or "manual", url))
    conn.commit()


def reset_failed() -> int:
    """Reset all failed jobs so they can be retried.

    Returns:
        Number of jobs reset.
    """
    conn = get_connection()
    cursor = conn.execute("""
        UPDATE jobs SET apply_status = NULL, apply_error = NULL,
                       apply_attempts = 0, agent_id = NULL
        WHERE apply_status = 'failed'
          OR (apply_status IS NOT NULL AND apply_status != 'applied'
              AND apply_status != 'duplicate_applied'
              AND apply_status != 'in_progress')
    """)
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Per-job execution
# ---------------------------------------------------------------------------

def run_job(job: dict, port: int, worker_id: int = 0,
            model: str | None = None, dry_run: bool = False) -> tuple[str, int]:
    """Spawn one Codex CLI session for a job application.

    Returns:
        Tuple of (status_string, duration_ms). Status is one of:
        'applied', 'dry_run', 'expired', 'captcha', 'login_issue',
        'failed:reason', or 'skipped'.
    """
    # Read tailored resume text
    resume_path = job.get("tailored_resume_path")
    txt_path = Path(resume_path).with_suffix(".txt") if resume_path else None
    resume_text = ""
    if txt_path and txt_path.exists():
        resume_text = txt_path.read_text(encoding="utf-8")

    # Build the prompt
    agent_prompt = prompt_mod.build_prompt(
        job=job,
        tailored_resume=resume_text,
        dry_run=dry_run,
    )

    worker_dir = reset_worker_dir(worker_id)

    update_state(worker_id, status="applying", job_title=job["title"],
                 company=job.get("site", ""), score=job.get("fit_score", 0),
                 start_time=time.time(), actions=0, last_action="starting")
    add_event(f"[W{worker_id}] Starting: {job['title'][:40]} @ {job.get('site', '')}")

    worker_log = config.LOG_DIR / f"worker-{worker_id}.log"
    ts_header = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_header = (
        f"\n{'=' * 60}\n"
        f"[{ts_header}] {job['title']} @ {job.get('site', '')}\n"
        f"URL: {job.get('application_url') or job['url']}\n"
        f"Score: {job.get('fit_score', 'N/A')}/10\n"
        f"{'=' * 60}\n"
    )

    start = time.time()
    result_data: CodexResult | None = None

    def _track_process(proc: subprocess.Popen | None) -> None:
        with _codex_lock:
            if proc is None:
                _codex_procs.pop(worker_id, None)
            else:
                _codex_procs[worker_id] = proc

    def _handle_event(event: dict) -> None:
        if event.get("type") != "item.completed":
            return
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
            return
        name = item.get("name") or item.get("tool") or "MCP tool"
        server = item.get("server")
        if server:
            name = f"{server}:{name}"
        arguments = item.get("arguments") or item.get("input") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if isinstance(arguments, dict):
            if arguments.get("url"):
                detail = str(arguments["url"])[:60]
            elif arguments.get("ref"):
                detail = str(arguments.get("element") or arguments.get("text") or arguments["ref"])[:45]
            elif arguments.get("fields"):
                detail = f"({len(arguments['fields'])} fields)"
            elif arguments.get("paths"):
                detail = "upload"
            else:
                detail = ""
        else:
            detail = ""
        action = f"{name} {detail}".strip()[:70]
        add_event(f"[W{worker_id}] {action}")
        ws = get_state(worker_id)
        update_state(worker_id, actions=(ws.actions if ws else 0) + 1,
                     last_action=action[:35])

    with _codex_lock:
        _skip_workers.discard(worker_id)

    try:
        with open(worker_log, "a", encoding="utf-8") as lf:
            lf.write(log_header)
        result_data = run_codex_exec(
            agent_prompt,
            model=model or os.environ.get("CODEX_MODEL") or None,
            config_overrides=_codex_mcp_overrides(port, dry_run=dry_run),
            timeout_s=300,
            cwd=str(worker_dir),
            on_event=_handle_event,
            on_process=_track_process,
        )
        duration_ms = int((time.time() - start) * 1000)
        output = result_data.final_text

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_log = config.LOG_DIR / f"codex_{ts}_w{worker_id}_{job.get('site', 'unknown')[:20]}.txt"
        job_log.write_text(output, encoding="utf-8")

        if result_data.usage is not None or result_data.turns:
            ws = get_state(worker_id)
            previous_turns = ws.total_turns if ws else 0
            previous_input = ws.total_input_tokens if ws else 0
            previous_output = ws.total_output_tokens if ws else 0
            usage = result_data.usage or {}
            update_state(
                worker_id,
                total_turns=previous_turns + result_data.turns,
                total_input_tokens=previous_input + usage.get("input_tokens", 0),
                total_output_tokens=previous_output + usage.get("output_tokens", 0),
            )

        with open(worker_log, "a", encoding="utf-8") as lf:
            lf.write(output + "\n")

        def _clean_reason(s: str) -> str:
            return re.sub(r'[*`"]+$', '', s).strip()

        if "RESULT:DRY_RUN" in output:
            add_event(f"[W{worker_id}] PREVIEW COMPLETE: {job['title'][:34]}")
            update_state(worker_id, status="dry_run", last_action="preview complete")
            return "dry_run", duration_ms

        if dry_run and "RESULT:APPLIED" in output:
            add_event(f"[W{worker_id}] Unexpected submission result in preview")
            update_state(worker_id, status="failed", last_action="unexpected result in preview")
            return "failed:dry_run_unexpected_result", duration_ms

        elapsed = int((time.time() - start))
        for result_status in ["APPLIED", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"]:
            if f"RESULT:{result_status}" in output:
                add_event(f"[W{worker_id}] {result_status} ({elapsed}s): {job['title'][:30]}")
                update_state(worker_id, status=result_status.lower(),
                             last_action=f"{result_status} ({elapsed}s)")
                return result_status.lower(), duration_ms

        if "RESULT:FAILED" in output:
            for out_line in output.split("\n"):
                if "RESULT:FAILED" in out_line:
                    reason = out_line.split("RESULT:FAILED:", 1)[-1].strip()
                    if reason == out_line.strip() or not reason:
                        reason = "unknown"
                    reason = _clean_reason(reason)
                    PROMOTE_TO_STATUS = {"captcha", "expired", "login_issue"}
                    if reason in PROMOTE_TO_STATUS:
                        add_event(f"[W{worker_id}] {reason.upper()} ({elapsed}s): {job['title'][:30]}")
                        update_state(worker_id, status=reason,
                                     last_action=f"{reason.upper()} ({elapsed}s)")
                        return reason, duration_ms
                    add_event(f"[W{worker_id}] FAILED ({elapsed}s): {reason[:30]}")
                    update_state(worker_id, status="failed",
                                 last_action=f"FAILED: {reason[:25]}")
                    return f"failed:{reason}", duration_ms
            return "failed:unknown", duration_ms

        add_event(f"[W{worker_id}] NO RESULT ({elapsed}s)")
        update_state(worker_id, status="failed", last_action=f"no result ({elapsed}s)")
        return "failed:no_result_line", duration_ms

    except CodexExecutionError as e:
        with _codex_lock:
            skipped = worker_id in _skip_workers
        duration_ms = int((time.time() - start) * 1000)
        if skipped:
            add_event(f"[W{worker_id}] Job skipped")
            update_state(worker_id, status="idle", last_action="skipped")
            return "skipped", duration_ms
        add_event(f"[W{worker_id}] CODEX ERROR: {str(e)[:45]}")
        update_state(worker_id, status="failed", last_action=f"CODEX: {str(e)[:22]}")
        return f"failed:codex:{str(e)[:80]}", duration_ms
    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        elapsed = int(time.time() - start)
        add_event(f"[W{worker_id}] TIMEOUT ({elapsed}s)")
        update_state(worker_id, status="failed", last_action=f"TIMEOUT ({elapsed}s)")
        return "failed:timeout", duration_ms
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        add_event(f"[W{worker_id}] ERROR: {str(e)[:40]}")
        update_state(worker_id, status="failed", last_action=f"ERROR: {str(e)[:25]}")
        return f"failed:{str(e)[:100]}", duration_ms
    finally:
        _track_process(None)


# ---------------------------------------------------------------------------
# Permanent failure classification
# ---------------------------------------------------------------------------

PERMANENT_FAILURES: set[str] = {
    "expired", "captcha", "login_issue",
    "not_eligible_location", "not_eligible_salary",
    "already_applied", "account_required",
    "not_a_job_application", "unsafe_permissions",
    "unsafe_verification", "sso_required",
    "site_blocked", "cloudflare_blocked", "blocked_by_cloudflare",
    "manual_review_required",
}

PERMANENT_PREFIXES: tuple[str, ...] = ("site_blocked", "cloudflare", "blocked_by")


def _is_permanent_failure(result: str) -> bool:
    """Determine if a failure should never be retried."""
    reason = result.split(":", 1)[-1] if ":" in result else result
    return (
        result in PERMANENT_FAILURES
        or reason in PERMANENT_FAILURES
        or any(reason.startswith(p) for p in PERMANENT_PREFIXES)
    )


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

def worker_loop(worker_id: int = 0, limit: int = 1,
                target_url: str | None = None,
                min_score: int = 7, headless: bool = False,
                model: str | None = None, dry_run: bool = False) -> tuple[int, int]:
    """Run jobs sequentially until limit is reached or queue is empty.

    Args:
        worker_id: Numeric worker identifier.
        limit: Max jobs to process (0 = continuous).
        target_url: Apply to a specific URL.
        min_score: Minimum fit_score threshold.
        headless: Run Chrome headless.
        model: Optional Codex model override.
        dry_run: Preview the application without submitting or changing saved status.

    Returns:
        Tuple of (applied_count, failed_count).
    """
    applied = 0
    failed = 0
    continuous = limit == 0
    jobs_done = 0
    empty_polls = 0
    port = BASE_CDP_PORT + worker_id

    while not _stop_event.is_set():
        if not continuous and jobs_done >= limit:
            break

        update_state(worker_id, status="idle", job_title="", company="",
                     last_action="waiting for job", actions=0)

        job = acquire_job(target_url=target_url, min_score=min_score,
                          worker_id=worker_id, dry_run=dry_run)
        if not job:
            if not continuous:
                add_event(f"[W{worker_id}] Queue empty")
                update_state(worker_id, status="done", last_action="queue empty")
                break
            empty_polls += 1
            update_state(worker_id, status="idle",
                         last_action=f"polling ({empty_polls})")
            if empty_polls == 1:
                add_event(f"[W{worker_id}] Queue empty, polling every {POLL_INTERVAL}s...")
            # Use Event.wait for interruptible sleep
            if _stop_event.wait(timeout=POLL_INTERVAL):
                break  # Stop was requested during wait
            continue

        empty_polls = 0

        if job.get("_manual_ats"):
            add_event(f"[W{worker_id}] Preview skipped: manual ATS requires manual review")
            update_state(worker_id, status="manual_review",
                         job_title=job["title"], company=job.get("site", ""),
                         last_action="manual ATS requires review")
            jobs_done += 1
            update_state(worker_id, jobs_done=jobs_done)
            if target_url:
                break
            continue

        if job.get("_salary_ineligible"):
            reason = job.get("_salary_reason", "posted pay is below the saved salary floor")
            add_event(f"[W{worker_id}] Skipped below salary floor: {job['title'][:30]}")
            update_state(
                worker_id,
                status="not_eligible",
                job_title=job["title"],
                company=job.get("site", ""),
                last_action=reason[:80],
            )
            jobs_done += 1
            update_state(worker_id, jobs_done=jobs_done)
            if target_url:
                break
            continue

        chrome_proc = None
        try:
            add_event(f"[W{worker_id}] Launching Chrome...")
            chrome_proc = launch_chrome(
                worker_id,
                port=port,
                headless=headless,
                initial_url=(job.get("application_url") or job["url"]) if dry_run else None,
                fresh_profile=dry_run,
            )

            result, duration_ms = run_job(job, port=port, worker_id=worker_id,
                                            model=model, dry_run=dry_run)

            if dry_run:
                if result == "skipped":
                    add_event(f"[W{worker_id}] Preview skipped: {job['title'][:30]}")
                    update_state(worker_id, status="idle", last_action="preview skipped")
                elif result == "dry_run":
                    update_state(worker_id, jobs_done=jobs_done + 1)
                else:
                    failed += 1
                    update_state(worker_id, jobs_failed=failed,
                                 jobs_done=jobs_done + 1)
            elif result == "skipped":
                release_lock(job["url"])
                add_event(f"[W{worker_id}] Skipped: {job['title'][:30]}")
            elif result == "applied":
                mark_result(job["url"], "applied", duration_ms=duration_ms)
                applied += 1
                update_state(worker_id, jobs_applied=applied,
                             jobs_done=applied + failed)
            else:
                reason = result.split(":", 1)[-1] if ":" in result else result
                mark_result(job["url"], "failed", reason,
                            permanent=_is_permanent_failure(result),
                            duration_ms=duration_ms)
                failed += 1
                update_state(worker_id, jobs_failed=failed,
                             jobs_done=applied + failed)

        except KeyboardInterrupt:
            if not dry_run:
                release_lock(job["url"])
            if _stop_event.is_set():
                pass
            else:
                add_event(f"[W{worker_id}] Job skipped (Ctrl+C)")
                update_state(worker_id, status="idle", last_action="skipped")
        except Exception as e:
            logger.exception("Worker %d launcher error", worker_id)
            add_event(f"[W{worker_id}] Launcher error: {str(e)[:40]}")
            if not dry_run:
                release_lock(job["url"])
            failed += 1
            update_state(worker_id, jobs_failed=failed,
                         jobs_done=jobs_done + 1)
        finally:
            try:
                if chrome_proc:
                    cleanup_worker(worker_id, chrome_proc)
            finally:
                if dry_run:
                    release_lock(
                        job["url"],
                        restore_previous=True,
                        restore_status=job.get("_previous_apply_status"),
                        restore_agent_id=job.get("_previous_agent_id"),
                        restore_last_attempted_at=job.get("_previous_last_attempted_at"),
                    )

        jobs_done += 1
        update_state(worker_id, jobs_done=jobs_done)
        if target_url:
            break

    update_state(worker_id, status="done", last_action="finished")
    return applied, failed


# ---------------------------------------------------------------------------
# Main entry point (called from cli.py)
# ---------------------------------------------------------------------------

def main(limit: int = 1, target_url: str | None = None,
         min_score: int = 7, headless: bool = False, model: str | None = None,
         dry_run: bool = False, continuous: bool = False,
         poll_interval: int = 60, workers: int = 1) -> None:
    """Launch the apply pipeline.

    Args:
        limit: Max jobs to apply to (0 or with continuous=True means run forever).
        target_url: Apply to a specific URL.
        min_score: Minimum fit_score threshold.
        headless: Run Chrome in headless mode.
        model: Optional Codex model override.
        dry_run: Preview without submitting or changing saved status.
        continuous: Run forever, polling for new jobs.
        poll_interval: Seconds between DB polls when queue is empty.
        workers: Number of parallel workers (default 1).
    """
    global POLL_INTERVAL
    POLL_INTERVAL = poll_interval
    _stop_event.clear()

    config.ensure_dirs()
    console = Console()

    if continuous:
        effective_limit = 0
        mode_label = "continuous"
    else:
        effective_limit = limit
        mode_label = f"{limit} jobs"

    # Initialize dashboard for all workers
    for i in range(workers):
        init_worker(i)

    worker_label = f"{workers} worker{'s' if workers > 1 else ''}"
    console.print(f"Launching apply pipeline ({mode_label}, {worker_label}, poll every {POLL_INTERVAL}s)...")
    console.print("[dim]Ctrl+C = skip current job(s) | Ctrl+C x2 = stop[/dim]")

    # Double Ctrl+C handler
    _ctrl_c_count = 0

    def _sigint_handler(sig, frame):
        nonlocal _ctrl_c_count
        _ctrl_c_count += 1
        if _ctrl_c_count == 1:
            console.print("\n[yellow]Skipping current job(s)... (Ctrl+C again to STOP)[/yellow]")
            # Kill active Codex processes to skip the current jobs.
            with _codex_lock:
                for wid, cproc in list(_codex_procs.items()):
                    if cproc.poll() is None:
                        _skip_workers.add(wid)
                        _kill_process_tree(cproc.pid)
        else:
            console.print("\n[red bold]STOPPING[/red bold]")
            _stop_event.set()
            with _codex_lock:
                for wid, cproc in list(_codex_procs.items()):
                    if cproc.poll() is None:
                        _skip_workers.add(wid)
                        _kill_process_tree(cproc.pid)
            kill_all_chrome()
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        with Live(render_full(), console=console, refresh_per_second=2) as live:
            # Daemon thread for display refresh only (no business logic)
            _dashboard_running = True

            def _refresh():
                while _dashboard_running:
                    live.update(render_full())
                    time.sleep(0.5)

            refresh_thread = threading.Thread(target=_refresh, daemon=True)
            refresh_thread.start()

            if workers == 1:
                # Single worker — run directly in main thread
                total_applied, total_failed = worker_loop(
                    worker_id=0,
                    limit=effective_limit,
                    target_url=target_url,
                    min_score=min_score,
                    headless=headless,
                    model=model,
                    dry_run=dry_run,
                )
            else:
                # Multi-worker — distribute limit across workers
                if effective_limit:
                    base = effective_limit // workers
                    extra = effective_limit % workers
                    limits = [base + (1 if i < extra else 0)
                              for i in range(workers)]
                else:
                    limits = [0] * workers  # continuous mode

                with ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="apply-worker") as executor:
                    futures = {
                        executor.submit(
                            worker_loop,
                            worker_id=i,
                            limit=limits[i],
                            target_url=target_url,
                            min_score=min_score,
                            headless=headless,
                            model=model,
                            dry_run=dry_run,
                        ): i
                        for i in range(workers)
                    }

                    results: list[tuple[int, int]] = []
                    for future in as_completed(futures):
                        wid = futures[future]
                        try:
                            results.append(future.result())
                        except Exception:
                            logger.exception("Worker %d crashed", wid)
                            results.append((0, 0))

                total_applied = sum(r[0] for r in results)
                total_failed = sum(r[1] for r in results)

            _dashboard_running = False
            refresh_thread.join(timeout=2)
            live.update(render_full())

        totals = get_totals()
        console.print(
            f"\n[bold]Done: {total_applied} applied, {total_failed} failed "
            f"(cost N/A, {totals['input_tokens']:,} input + "
            f"{totals['output_tokens']:,} output tokens, {totals['turns']} Codex turns)[/bold]"
        )
        console.print(f"Logs: {config.LOG_DIR}")

    except KeyboardInterrupt:
        pass
    finally:
        _stop_event.set()
        kill_all_chrome()
