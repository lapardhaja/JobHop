> **JobHop** is an independent local fork of [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot), the AGPL-3.0-only open-source project created by [Pickle-Pixel](https://github.com/Pickle-Pixel). This fork adds a Codex CLI backend and JobHop branding. The upstream ApplyPilot repository is unchanged by this fork.

# JobHop

**Codex-powered job search and application workflow. Open source.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)




https://github.com/user-attachments/assets/7ee3417f-43d4-4245-9952-35df1e77f2df


---

## What It Does

JobHop is a 6-stage job application pipeline. It discovers jobs across 5+ boards, scores them against your resume with AI, tailors your resume per job, writes cover letters, and can submit applications through a browser workflow.

Install this source checkout in its own virtual environment, then run the pipeline:

See the [Windows Quick Start](docs/QUICKSTART.md) for complete installation, Codex sign-in, profile setup, citywide location filtering, and a safe first-run walkthrough. Common setup issues are covered in [Troubleshooting](docs/TROUBLESHOOTING.md).

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux:        source .venv/bin/activate
python -m pip install -e .
python -m pip install --no-deps python-jobspy
python -m pip install pydantic tls-client requests markdownify regex curl_cffi
jobhop init          # one-time setup: resume, profile, preferences, Codex or API backend
jobhop doctor        # verify your setup — shows what's installed and what's missing
jobhop run           # discover > enrich > score > tailor > cover letters
jobhop run -w 4      # same but parallel (4 threads for discovery/enrichment)
jobhop apply         # browser-driven application workflow
jobhop apply -w 3    # parallel apply (3 Chrome instances)
jobhop apply --dry-run --limit 1  # preview one job without submitting or changing status
```

> **Why two install commands?** `python-jobspy` pins an exact numpy version in its metadata that conflicts with pip's resolver, but works fine at runtime with any modern numpy. The `--no-deps` flag bypasses the resolver; the second command installs jobspy's actual runtime dependencies. Everything except `python-jobspy` installs normally.

On Windows, activate the environment in PowerShell before running `jobhop`. This source checkout has a separate environment from any earlier pip installation. By default, JobHop and the upstream ApplyPilot installation use the same `%USERPROFILE%\.applypilot` profile, database, and job data; set `APPLYPILOT_DIR` to use a different data directory.

---

## Two Paths

### Full Pipeline (recommended)
**Requires:** Python 3.11+, signed-in Codex CLI, Node.js 18+ (`npx`), Chrome

Runs all 6 stages, from job discovery to browser-assisted application submission.

### Discovery + Tailoring Only
**Requires:** Python 3.11+ and signed-in Codex CLI (default), or an explicitly selected API backend

Runs stages 1-5: discovers jobs, scores them, tailors your resume, generates cover letters. You submit applications manually with the AI-prepared materials.

---

## The Pipeline

| Stage | What Happens |
|-------|-------------|
| **1. Discover** | Scrapes 5 job boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs) + 48 Workday employer portals + 30 direct career sites |
| **2. Enrich** | Fetches full job descriptions via JSON-LD, CSS selectors, or AI-powered extraction |
| **3. Score** | AI rates every job 1-10 based on your resume and preferences. Only high-fit jobs proceed |
| **4. Tailor** | AI rewrites your resume per job: reorganizes, emphasizes relevant experience, adds keywords. Never fabricates |
| **5. Cover Letter** | AI generates a targeted cover letter per job |
| **6. Auto-Apply** | Codex CLI uses worker-isolated Playwright tools to navigate forms, fill fields, upload documents, and answer supported questions |

Each stage is independent. Run them all or pick what you need.

---

## JobHop vs The Alternatives

| Feature | JobHop | AIHawk | Manual |
|---------|-----------|--------|--------|
| Job discovery | 5 boards + Workday + direct sites | LinkedIn only | One board at a time |
| AI scoring | 1-10 fit score per job | Basic filtering | Your gut feeling |
| Resume tailoring | Per-job AI rewrite | Template-based | Hours per application |
| Auto-apply | Full form navigation + submission | LinkedIn Easy Apply only | Click, type, repeat |
| Supported sites | Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, 46 Workday portals, 28 direct sites | LinkedIn | Whatever you open |
| License | AGPL-3.0 | MIT | N/A |

---

## Requirements

| Component | Required For | Details |
|-----------|-------------|---------|
| Python 3.11+ | Everything | Core runtime |
| Codex CLI + sign-in | Scoring, tailoring, cover letters, auto-apply | Uses local Codex CLI login by default; no model API key is needed |
| Node.js 18+ (`npx`) | Auto-apply | Runs the Playwright MCP server |
| Chrome/Chromium | Auto-apply | Auto-detected on most systems |
| API provider (optional) | Scoring, tailoring, cover letters | Gemini, OpenAI, or a local OpenAI-compatible server; used only with `APPLYPILOT_AI_PROVIDER=api` |

To use Codex, install the [Codex CLI](https://developers.openai.com/codex/cli) and sign in with `codex login`. Codex is the default even when API keys exist in `.env`; there is no automatic provider switch. API mode accepts Gemini, OpenAI, or local API-compatible providers.

### Optional

| Component | What It Does |
|-----------|-------------|
| CapSolver API key | Solves CAPTCHAs during auto-apply (hCaptcha, reCAPTCHA, Turnstile, FunCaptcha). Without it, CAPTCHA-blocked applications just fail gracefully |

> **Note:** python-jobspy is installed separately with `--no-deps` because it pins an exact numpy version in its metadata that conflicts with pip's resolver. It works fine with modern numpy at runtime.

---

## Configuration

All generated by `jobhop init`:

### `profile.json`
Your personal data in one structured file: contact info, work authorization, compensation, experience, skills, resume facts (preserved during tailoring), and EEO defaults. Powers scoring, tailoring, and form auto-fill.

### `searches.yaml`
Job search queries, target titles, locations, boards. Run multiple searches with different parameters.

### `.env`
Backend and runtime config: `APPLYPILOT_AI_PROVIDER=codex` (default), optional `CODEX_MODEL`, and optional API settings. To use an API provider, set `APPLYPILOT_AI_PROVIDER=api`; `APPLYPILOT_API_PROVIDER` can select `gemini`, `openai`, or `local`. `CAPSOLVER_API_KEY` and `PROXY` remain optional runtime settings.

### Package configs (shipped with JobHop)
- `config/employers.yaml` - Workday employer registry (48 preconfigured)
- `config/sites.yaml` - Direct career sites (30+), blocked sites, base URLs, manual ATS domains
- `config/searches.example.yaml` - Example search configuration

---

## How Stages Work

### Discover
Queries Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs via JobSpy. Scrapes 48 Workday employer portals (configurable in `employers.yaml`). Hits 30 direct career sites with custom extractors. Deduplicates by URL.

### Enrich
Visits each job URL and extracts the full description. 3-tier cascade: JSON-LD structured data, then CSS selector patterns, then AI-powered extraction for unknown layouts.

### Score
AI scores every job 1-10 against your profile. 9-10 = strong match, 7-8 = good, 5-6 = moderate, 1-4 = skip. Only jobs above your threshold proceed to tailoring.

### Tailor
Generates a custom resume per job: reorders experience, emphasizes relevant skills, incorporates keywords from the job description. Your `resume_facts` (companies, projects, metrics) are preserved exactly. The AI reorganizes but never fabricates.

### Cover Letter
Writes a targeted cover letter per job referencing the specific company, role, and how your experience maps to their requirements.

### Auto-Apply
Codex CLI launches a worker-specific Chrome instance, uses its scoped Playwright MCP server to navigate application pages, fill forms, and upload the tailored resume and cover letter. It answers questions only from facts supported by your profile and resume; if a required answer is unsupported, it stops for manual review. A live dashboard shows progress in real time.

The Playwright MCP server is configured per worker at runtime, and the run does not change global Codex settings. Codex's shell sandbox stays read-only for live workers; preview runs disable shell/file access, native browser/computer-use, connected apps/plugins, code mode, subagents, and unrelated browser inspection actions.

Start with `jobhop apply --dry-run --limit 1`. JobHop opens the job URL in a clean temporary Chrome profile before starting Codex, then Codex inspects the visible page. It receives only an allowlist of browser inspection tools; it cannot navigate elsewhere, use form actions or page code, or access Gmail. Local shell/file access, native browser/computer-use, connected apps/plugins, code mode, and subagent tools are disabled. Raw resume/cover-letter text and files, contact details, credentials, and document paths are withheld; only structured professional facts are provided for comparison. Preview never submits an application, creates an employer account, or changes the job's saved application status. A live run can fill and submit forms after you choose to run it without `--dry-run`.

```bash
# Utility modes (no browser or Codex session needed)
jobhop apply --mark-applied URL    # manually mark a job as applied
jobhop apply --mark-failed URL     # manually mark a job as failed
jobhop apply --reset-failed        # reset all failed jobs for retry
jobhop apply --gen --url URL       # generate prompt file for manual debugging
```

---

## CLI Reference

```
jobhop init                         # First-time setup wizard
jobhop doctor                       # Verify setup, diagnose missing requirements
jobhop run [stages...]              # Run pipeline stages (or 'all')
jobhop run --workers 4              # Parallel discovery/enrichment
jobhop run --stream                 # Concurrent stages (streaming mode)
jobhop run --min-score 8            # Override score threshold
jobhop run --dry-run                # Preview without executing
jobhop run --validation lenient     # Relax validation (recommended for Gemini free tier)
jobhop run --validation strict      # Strictest validation (retries on any banned word)
jobhop apply                        # Launch auto-apply
jobhop apply --workers 3            # Parallel browser workers
jobhop apply --dry-run --limit 1     # Preview one job without submit, email, account, or status changes
jobhop apply --continuous           # Run forever, polling for new jobs
jobhop apply --headless             # Headless browser mode
jobhop apply --url URL              # Apply to a specific job
jobhop status                       # Pipeline statistics
jobhop dashboard                    # Open HTML results dashboard
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

---

## License

JobHop is a local fork of ApplyPilot and is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-only).

You are free to use, modify, and distribute this software. If you deploy a modified version as a service, you must release your source code under the same license.
