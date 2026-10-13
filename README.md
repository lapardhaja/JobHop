# JobHop

**A local-first job search and application assistant powered by Codex.**

JobHop finds roles, compares them with your experience, prepares tailored application materials, and can help complete application forms in a browser. Your profile, resume, generated files, and job history stay on your computer.

## Get started

See the [Quick Start](docs/QUICKSTART.md) for Windows, macOS, and Linux setup. It covers Python, Codex sign-in, browser dependencies, profile setup, and a safe first run.

    python -m venv .venv
    # Windows PowerShell: .\.venv\Scripts\Activate.ps1
    # macOS/Linux:        source .venv/bin/activate
    python -m pip install -e .
    python -m pip install --no-deps python-jobspy
    python -m pip install pydantic tls-client requests markdownify regex curl_cffi
    python -m playwright install chromium
    jobhop init
    jobhop doctor
    jobhop run
    jobhop status

JobSpy is installed separately because its package metadata pins a NumPy version that conflicts with pip's resolver. The remaining commands install its runtime dependencies.

## What it does

1. Finds jobs from job boards, employer career sites, and Workday portals.
2. Enriches listings with full descriptions when available.
3. Scores roles against your resume and preferences.
4. Tailors your resume while preserving supported facts.
5. Prepares a role-specific cover letter.
6. Uses Codex CLI and Playwright for browser-assisted applications.

Codex CLI is the default for every AI stage. Sign in with the Codex CLI; no model API key is required. API backends can be selected explicitly in the local configuration.

## Start with a preview

Run discovery and prepare materials first:

    jobhop run
    jobhop status
    jobhop apply --dry-run --limit 1

The dry run previews one application without submitting it or changing its saved status. It opens the listing in a clean browser profile and gives Codex a restricted set of page inspection tools. It withholds raw resume and cover-letter files, contact details, credentials, and document paths. JobHop only answers application questions supported by your profile and resume; unsupported required questions stop for manual review.

Running jobhop apply without --dry-run can submit applications. Review the preview and the site's form before choosing a live run.

## Data and settings

JobHop stores private files in %USERPROFILE%\.jobhop on Windows or ~/.jobhop on macOS and Linux. Set JOBHOP_DIR to choose another location.

- profile.json stores your application profile and supported resume facts.
- resume.pdf and resume.txt store the source resume.
- searches.yaml stores roles, locations, boards, and search preferences.
- .env stores backend and optional service settings.
- jobhop.db tracks listings and pipeline progress.

The repository ignores these files and generated application materials. Do not commit personal profile data, resumes, API keys, or job history.

The default backend settings are:

    JOBHOP_AI_PROVIDER=codex
    # JOBHOP_AI_PROVIDER=api
    # JOBHOP_API_PROVIDER=gemini

API mode supports Gemini, OpenAI, and local OpenAI-compatible endpoints. CODEX_MODEL is an optional model override. CAPSOLVER_API_KEY and PROXY are optional.

## Commands

    jobhop init                         # Create or update your local profile
    jobhop doctor                       # Check local setup
    jobhop run                          # Discover and prepare jobs
    jobhop run --workers 4              # Parallel discovery/enrichment
    jobhop run --min-score 8            # Raise the fit threshold
    jobhop apply --dry-run --limit 1    # Preview one application
    jobhop apply                        # Start a live application run
    jobhop status                       # Show pipeline progress
    jobhop dashboard                    # Open the local results dashboard

## Help and contributions

See [Troubleshooting](docs/TROUBLESHOOTING.md) for common setup and discovery issues. See [Contributing](CONTRIBUTING.md) for development guidance.

## License and notices

JobHop is distributed under the [GNU Affero General Public License v3.0](LICENSE). Read [NOTICE](NOTICE.md) for modification and source attribution details.
