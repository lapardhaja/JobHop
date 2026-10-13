"""JobHop first-time setup wizard.

Interactive flow that creates ~/.jobhop/ with:
  - resume.txt (and optionally resume.pdf)
  - profile.json
  - searches.yaml
  - .env (Codex/API backend settings)
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from jobhop.config import (
    APP_DIR,
    ENV_PATH,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    ensure_dirs,
)

console = Console()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def _setup_resume() -> None:
    """Prompt for resume file and copy into APP_DIR."""
    console.print(Panel("[bold]Step 1: Resume[/bold]\nPoint to your master resume file (.txt or .pdf)."))

    while True:
        path_str = Prompt.ask("Resume file path")
        src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()

        if not src.exists():
            console.print(f"[red]File not found:[/red] {src}")
            continue

        suffix = src.suffix.lower()
        if suffix not in (".txt", ".pdf"):
            console.print("[red]Unsupported format.[/red] Provide a .txt or .pdf file.")
            continue

        if suffix == ".txt":
            shutil.copy2(src, RESUME_PATH)
            console.print(f"[green]Copied to {RESUME_PATH}[/green]")
        elif suffix == ".pdf":
            shutil.copy2(src, RESUME_PDF_PATH)
            console.print(f"[green]Copied to {RESUME_PDF_PATH}[/green]")

            # Also ask for a plain-text version for LLM consumption
            txt_path_str = Prompt.ask(
                "Plain-text version of your resume (.txt)",
                default="",
            )
            if txt_path_str.strip():
                txt_src = Path(txt_path_str.strip().strip('"').strip("'")).expanduser().resolve()
                if txt_src.exists():
                    shutil.copy2(txt_src, RESUME_PATH)
                    console.print(f"[green]Copied to {RESUME_PATH}[/green]")
                else:
                    console.print("[yellow]File not found, skipping plain-text copy.[/yellow]")
        break


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _split_list(value: str) -> list[str]:
    """Split wizard lists on semicolons so commas remain part of values."""
    return [item.strip() for item in value.split(";") if item.strip()]


def _parse_salary_range(value: str) -> tuple[str, str]:
    """Parse a closed range or an open-ended minimum from the wizard."""
    value = value.strip()
    if not value:
        return "", ""
    if value.endswith("+"):
        return value[:-1].strip(), ""
    if "-" in value:
        lower, upper = value.split("-", 1)
        return lower.strip(), upper.strip()
    return value, value


def _setup_profile() -> dict:
    """Walk through profile questions and return a nested profile dict."""
    console.print(Panel("[bold]Step 2: Profile[/bold]\nTell JobHop about yourself. This powers scoring, tailoring, and auto-fill."))

    profile: dict = {}

    # -- Personal --
    console.print("\n[bold cyan]Personal Information[/bold cyan]")
    full_name = Prompt.ask("Full name")
    profile["personal"] = {
        "full_name": full_name,
        "preferred_name": Prompt.ask("Preferred/nickname (leave blank to use first name)", default=""),
        "email": Prompt.ask("Email address"),
        "phone": Prompt.ask("Phone number", default=""),
        "city": Prompt.ask("City"),
        "province_state": Prompt.ask("Province/State (e.g. Ontario, California)", default=""),
        "country": Prompt.ask("Country"),
        "postal_code": Prompt.ask("Postal/ZIP code", default=""),
        "address": Prompt.ask("Street address (optional, used for form auto-fill)", default=""),
        "linkedin_url": Prompt.ask("LinkedIn URL", default=""),
        "github_url": Prompt.ask("GitHub URL (optional)", default=""),
        "portfolio_url": Prompt.ask("Portfolio URL (optional)", default=""),
        "website_url": Prompt.ask("Personal website URL (optional)", default=""),
        "password": Prompt.ask("Job site password (used for login walls during auto-apply)", password=True, default=""),
    }

    # -- Work Authorization --
    console.print("\n[bold cyan]Work Authorization[/bold cyan]")
    profile["work_authorization"] = {
        "legally_authorized_to_work": Confirm.ask("Are you legally authorized to work in your target country?"),
        "require_sponsorship": Confirm.ask("Will you now or in the future need sponsorship?"),
        "work_permit_type": Prompt.ask("Work permit type (e.g. Citizen, PR, Open Work Permit — leave blank if N/A)", default=""),
    }

    # -- Compensation --
    console.print("\n[bold cyan]Compensation[/bold cyan]")
    salary = Prompt.ask("Expected annual salary (digits only, e.g. 80000)", default="")
    salary_currency = Prompt.ask("Currency", default="USD")
    salary_range = Prompt.ask(
        "Acceptable range (e.g. 80000-120000 or 80000+; leave blank if unsure)",
        default="",
    )
    range_min, range_max = _parse_salary_range(salary_range)
    profile["compensation"] = {
        "salary_expectation": salary,
        "salary_currency": salary_currency,
        "salary_range_min": range_min,
        "salary_range_max": range_max,
    }

    # -- Experience --
    console.print("\n[bold cyan]Experience[/bold cyan]")
    current_title = Prompt.ask("Current/most recent job title", default="")
    target_role = Prompt.ask("Target role (what you're applying for, e.g. 'Senior Backend Engineer')", default=current_title)
    profile["experience"] = {
        "years_of_experience_total": Prompt.ask("Years of professional experience", default=""),
        "education_level": Prompt.ask("Highest education (e.g. Bachelor's, Master's, PhD, Self-taught)", default=""),
        "current_title": current_title,
        "target_role": target_role,
    }

    # -- Skills Boundary --
    console.print("\n[bold cyan]Skills[/bold cyan] (semicolon-separated entries)")
    langs = Prompt.ask("Programming languages (separate entries with semicolons)", default="")
    frameworks = Prompt.ask("Frameworks & libraries (separate entries with semicolons)", default="")
    tools = Prompt.ask("Tools & platforms (separate entries with semicolons)", default="")
    profile["skills_boundary"] = {
        "programming_languages": _split_list(langs),
        "frameworks": _split_list(frameworks),
        "tools": _split_list(tools),
    }

    # -- Resume Facts (preserved truths for tailoring) --
    console.print("\n[bold cyan]Resume Facts[/bold cyan]")
    console.print("[dim]These are preserved exactly during resume tailoring — the AI will never change them.[/dim]")
    companies = Prompt.ask("Companies to always keep (separate entries with semicolons)", default="")
    projects = Prompt.ask("Projects to always keep (separate entries with semicolons)", default="")
    school = Prompt.ask("School name(s) to preserve (separate entries with semicolons)", default="")
    metrics = Prompt.ask("Real metrics to preserve (e.g. '99.9% uptime; 50k users')", default="")
    profile["resume_facts"] = {
        "preserved_companies": _split_list(companies),
        "preserved_projects": _split_list(projects),
        "preserved_school": school.strip(),
        "real_metrics": _split_list(metrics),
    }

    # -- EEO Voluntary (defaults) --
    profile["eeo_voluntary"] = {
        "gender": "Decline to self-identify",
        "race_ethnicity": "Decline to self-identify",
        "veteran_status": "Decline to self-identify",
        "disability_status": "Decline to self-identify",
    }

    # -- Availability --
    profile["availability"] = {
        "earliest_start_date": Prompt.ask("Earliest start date", default="Immediately"),
    }

    # Save
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"\n[green]Profile saved to {PROFILE_PATH}[/green]")
    return profile


# ---------------------------------------------------------------------------
# Search config
# ---------------------------------------------------------------------------

def _setup_searches() -> None:
    """Generate a searches.yaml from user input."""
    console.print(Panel("[bold]Step 3: Job Search Config[/bold]\nDefine what you're looking for."))

    location = Prompt.ask("Target location (e.g. 'Remote', 'Canada', 'New York, NY')", default="Remote")
    distance_str = Prompt.ask("Search radius in miles (0 for remote-only)", default="0")
    try:
        distance = int(distance_str)
    except ValueError:
        distance = 0

    roles_raw = Prompt.ask(
        "Target job titles (comma-separated, e.g. 'Backend Engineer, Full Stack Developer')"
    )
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    if not roles:
        console.print("[yellow]No roles provided. Using a default set.[/yellow]")
        roles = ["Software Engineer"]

    # Build YAML content
    lines = [
        "# JobHop search configuration",
        "# Edit this file to refine your job search queries.",
        "",
        "defaults:",
        f'  location: "{location}"',
        f"  distance: {distance}",
        "  hours_old: 72",
        "  results_per_site: 50",
        "",
        "locations:",
        f'  - location: "{location}"',
        f"    remote: {str(distance == 0).lower()}",
        "",
        "queries:",
    ]
    for i, role in enumerate(roles):
        lines.append(f'  - query: "{role}"')
        lines.append(f"    tier: {min(i + 1, 3)}")

    SEARCH_CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]Search config saved to {SEARCH_CONFIG_PATH}[/green]")


# ---------------------------------------------------------------------------
# AI Features
# ---------------------------------------------------------------------------

def _stored_env_values() -> dict[str, str]:
    """Read saved settings without printing or logging secret values."""
    from dotenv import dotenv_values

    return {key: str(value) for key, value in dotenv_values(ENV_PATH).items() if value is not None}


def _update_env_file(updates: dict[str, str | None]) -> None:
    """Update backend settings while preserving unrelated .env entries."""
    existing = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else [
        "# JobHop configuration",
    ]
    assignment = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    output: list[str] = []
    written: set[str] = set()

    for line in existing:
        match = assignment.match(line)
        key = match.group(1) if match else None
        if key not in updates:
            output.append(line)
            continue
        if key in written:
            continue
        written.add(key)
        value = updates[key]
        if value is not None:
            output.append(f"{key}={value}")

    for key, value in updates.items():
        if key not in written and value is not None:
            output.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def _setup_ai_features() -> None:
    """Ask about AI scoring/tailoring and configure its selected backend."""
    console.print(Panel(
        "[bold]Step 4: AI Features (optional)[/bold]\n"
        "Codex powers job scoring, resume tailoring, and cover letters by default.\n"
        "Without this, you can still discover and enrich jobs."
    ))

    if not Confirm.ask("Enable AI scoring and resume tailoring?", default=True):
        console.print("[dim]Discovery-only mode. You can configure AI later with [bold]jobhop init[/bold].[/dim]")
        return

    env_values = _stored_env_values()
    backend = Prompt.ask("Text backend", choices=["codex", "api"], default="codex").strip().lower()
    updates: dict[str, str | None] = {"JOBHOP_AI_PROVIDER": backend}

    if backend == "codex":
        model = Prompt.ask(
            "Codex model (leave blank for the Codex CLI default)",
            default=env_values.get("CODEX_MODEL", ""),
        ).strip()
        updates["CODEX_MODEL"] = model or None
    else:
        console.print(
            "API providers are used only while JOBHOP_AI_PROVIDER=api. "
            "Codex remains the default backend."
        )
        choices = ["gemini", "openai", "local"]
        saved_provider = env_values.get("JOBHOP_API_PROVIDER", "")
        default_provider = saved_provider if saved_provider in choices else "gemini"
        api_provider = Prompt.ask("API provider", choices=choices, default=default_provider).strip().lower()
        updates["JOBHOP_API_PROVIDER"] = api_provider

        if api_provider in {"gemini", "openai"}:
            key_name = "GEMINI_API_KEY" if api_provider == "gemini" else "OPENAI_API_KEY"
            provider_name = "Gemini" if api_provider == "gemini" else "OpenAI"
            saved_key = env_values.get(key_name, "")
            if saved_key and Confirm.ask(f"Keep the saved {provider_name} API key?", default=True):
                api_key = saved_key
            else:
                api_key = Prompt.ask(f"{provider_name} API key", password=True)
            default_model = "gemini-2.0-flash" if api_provider == "gemini" else "gpt-4o-mini"
            model = Prompt.ask("Model", default=env_values.get("LLM_MODEL", default_model)).strip()
            updates[key_name] = api_key
            updates["LLM_MODEL"] = model
        else:
            url = Prompt.ask(
                "Local API-compatible endpoint URL",
                default=env_values.get("LLM_URL", "http://localhost:8080/v1"),
            ).strip()
            model = Prompt.ask("Model name", default=env_values.get("LLM_MODEL", "local-model")).strip()
            updates["LLM_URL"] = url
            updates["LLM_MODEL"] = model

    _update_env_file(updates)
    console.print(f"[green]AI configuration saved to {ENV_PATH}[/green]")


# ---------------------------------------------------------------------------
# Auto-Apply
# ---------------------------------------------------------------------------

def _setup_auto_apply() -> None:
    """Configure autonomous job application (requires Codex CLI)."""
    console.print(Panel(
        "[bold]Step 5: Auto-Apply (optional)[/bold]\n"
        "JobHop can use Codex to fill and submit job applications\n"
        "using Codex CLI as the browser agent."
    ))

    if not Confirm.ask("Enable autonomous job applications?", default=True):
        console.print("[dim]You can apply manually using the tailored resumes JobHop generates.[/dim]")
        return

    # Check Codex CLI and the browser runtime.
    from jobhop.codex import codex_login_status, find_codex_cli

    codex_bin = find_codex_cli()
    if codex_bin:
        signed_in, _message = codex_login_status(codex_bin)
        if signed_in:
            console.print(f"[green]Codex CLI detected and signed in.[/green] [dim]{codex_bin}[/dim]")
        else:
            console.print("[yellow]Codex CLI is installed but not signed in.[/yellow] Run [bold]codex login[/bold].")
    else:
        console.print(
            "[yellow]Codex CLI not found on PATH.[/yellow]\n"
            "Install Codex CLI and sign in with [bold]codex login[/bold].\n"
            "Auto-apply won't work until Codex CLI is installed and signed in."
        )
    try:
        from jobhop.config import get_chrome_path
        get_chrome_path()
        console.print("[green]Chrome/Chromium detected.[/green]")
    except FileNotFoundError:
        console.print("[yellow]Chrome/Chromium is required for auto-apply.[/yellow]")
    if shutil.which("npx"):
        console.print("[green]Node.js/npx detected.[/green]")
    else:
        console.print("[yellow]Node.js 18+ and npx are required for the Playwright browser tools.[/yellow]")

    # Optional: CapSolver for CAPTCHAs
    console.print("\n[dim]Some job sites use CAPTCHAs. CapSolver can handle them automatically.[/dim]")
    if Confirm.ask("Configure CapSolver API key? (optional)", default=False):
        capsolver_key = Prompt.ask("CapSolver API key")
        # Append to existing .env or create
        if ENV_PATH.exists():
            existing = ENV_PATH.read_text(encoding="utf-8")
            if "CAPSOLVER_API_KEY" not in existing:
                ENV_PATH.write_text(
                    existing.rstrip() + f"\nCAPSOLVER_API_KEY={capsolver_key}\n",
                    encoding="utf-8",
                )
        else:
            ENV_PATH.write_text(f"# JobHop configuration\nCAPSOLVER_API_KEY={capsolver_key}\n", encoding="utf-8")
        console.print("[green]CapSolver key saved.[/green]")
    else:
        console.print("[dim]Skipped. Add CAPSOLVER_API_KEY to .env later if needed.[/dim]")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_wizard() -> None:
    """Run the full interactive setup wizard."""
    console.print()
    console.print(
        Panel.fit(
            "[bold green]JobHop Setup Wizard[/bold green]\n\n"
            "This will create your configuration at:\n"
            f"  [cyan]{APP_DIR}[/cyan]\n\n"
            "You can re-run this anytime with [bold]jobhop init[/bold].",
            border_style="green",
        )
    )

    ensure_dirs()
    console.print(f"[dim]Created {APP_DIR}[/dim]\n")

    # Step 1: Resume
    _setup_resume()
    console.print()

    # Step 2: Profile
    _setup_profile()
    console.print()

    # Step 3: Search config
    _setup_searches()
    console.print()

    # Step 4: AI features (optional)
    _setup_ai_features()
    console.print()

    # Step 5: Auto-apply (Codex CLI detection)
    _setup_auto_apply()
    console.print()

    # Done — show tier status
    from jobhop.config import get_tier, TIER_LABELS, TIER_COMMANDS

    tier = get_tier()

    tier_lines: list[str] = []
    for t in range(1, 4):
        label = TIER_LABELS[t]
        cmds = ", ".join(f"[bold]{c}[/bold]" for c in TIER_COMMANDS[t])
        if t <= tier:
            tier_lines.append(f"  [green]✓ Tier {t} — {label}[/green]  ({cmds})")
        elif t == tier + 1:
            tier_lines.append(f"  [yellow]→ Tier {t} — {label}[/yellow]  ({cmds})")
        else:
            tier_lines.append(f"  [dim]✗ Tier {t} — {label}  ({cmds})[/dim]")

    unlock_hint = ""
    if tier == 1:
        unlock_hint = "\n[dim]To unlock Tier 2: sign in to Codex CLI or select API mode and configure a provider.[/dim]"
    elif tier == 2:
        unlock_hint = "\n[dim]To unlock Tier 3: sign in to Codex CLI and install Chrome + Node.js/npx.[/dim]"

    console.print(
        Panel.fit(
            "[bold green]Setup complete![/bold green]\n\n"
            f"[bold]Your tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]\n\n"
            + "\n".join(tier_lines)
            + unlock_hint,
            border_style="green",
        )
    )
