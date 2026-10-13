# JobHop Quick Start

This guide installs the JobHop source checkout, connects its AI stages to your local Codex CLI session, and walks through a safe first run.

## Requirements

- Windows 10/11, macOS, or Linux
- Python 3.11 or newer
- Git
- Codex CLI installed and signed in (`codex login`)
- Node.js 18+ and `npx` for browser-assisted application workflows
- Chrome for browser-assisted application workflows

Description enrichment uses Playwright's Chromium build. Having Google Chrome installed does not install that separate browser binary.

## Install on Windows

Open PowerShell in the folder where you want the source checkout:

```powershell
git clone https://github.com/lapardhaja/JobHop.git
Set-Location JobHop
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install --no-deps python-jobspy
python -m pip install pydantic tls-client requests markdownify regex curl_cffi
python -m playwright install chromium
```

The separate `python-jobspy` commands work around its package metadata pinning a NumPy version that conflicts with the resolver; they install the runtime dependencies JobHop needs.

Check that the Codex CLI is available and signed in:

```powershell
codex --version
codex login
```

Install and sign in to Codex using the [official Codex CLI instructions](https://developers.openai.com/codex/cli). If `codex login` reports that you are already signed in, continue.

## Set up your local profile

Run the interactive wizard:

```powershell
jobhop init
jobhop doctor
```

The wizard asks for a resume, contact and application details, skills, preserved resume facts, target roles, location preferences, and AI provider. It copies the resume into JobHop's data directory. If you select a PDF, provide a plain-text version when prompted so scoring and tailoring can read it.

Enter salary values as digits (for example, `80000`). Use semicolons between list entries when an entry can contain a comma, such as a company or school name. For an open-ended salary minimum, enter a value like `80000+`; leave the range blank if you do not want to specify one.

The saved annual salary expectation is also a hard application floor: postings whose known maximum is below it are excluded from the apply queue. Hourly rates are annualized at 2,080 hours. Listings with missing or unclear pay remain visible for review, and salary answers must never fall below the saved floor. Fit scores measure role match and do not include salary eligibility; check the salary label in the dashboard.

By default, JobHop preserves the resume text without AI rewriting and copies the source PDF unchanged when one is available. This keeps job titles, section placement, education, certifications, and pagination intact. AI resume rewriting is opt-in with `allow_resume_rewriting: true` in the local profile; review any rewritten document before using it.

Codex is the default provider for scoring, tailoring, cover letters, and the browser stage. The local Codex CLI login is used; no model API key is needed. API providers are available only when explicitly selected with `JOBHOP_AI_PROVIDER=api` in the local `.env` file.

## Keep your data local

By default, JobHop stores your profile, resume, job database, searches, and generated documents in `%USERPROFILE%\.jobhop` on Windows or `~/.jobhop` on macOS/Linux.

Set `JOBHOP_DIR` before running `jobhop init` if you want a separate data directory. For the current PowerShell session:

```powershell
$env:JOBHOP_DIR = Join-Path $env:USERPROFILE "JobHopData"
jobhop init
```

The source checkout has its own virtual environment. The user data directory is separate from the checkout. Do not copy `profile.json`, resume files, `.env`, the database, or generated application documents into this repository. The checked-in `.gitignore` excludes these local files.

## Choose roles and locations

Edit `searches.yaml` in the local data directory after setup. You can keep separate search entries for an in-person area and remote work:

```yaml
locations:
  - location: "Example City, ST"
    remote: false
  - location: "Remote"
    remote: true

# Matching patterns are used to accept nearby districts or city names.
location_accept:
  - "Example City, ST"
  - "Example City"
  - "District A"
  - "District B"

location:
  accept_patterns:
    - "Example City, ST"
    - "Example City"
    - "District A"
    - "District B"
```

Replace the sample city and district names with the places you want. The wizard's distance field is a numeric search radius; it does not define a city boundary or automatically enumerate districts. For citywide coverage, include the city's common names and districts in the location patterns. Keep the separate `remote: true` entry if you also want remote roles.

The configuration also controls role queries, boards, age of postings, and excluded job titles. `src/jobhop/config/searches.example.yaml` has a fuller example.

Set `defaults.hours_old` to `720` to search the most recent 30 days. Increase `defaults.results_per_site` if you want a wider result set, and add exact title variants when a posting inserts words such as “AI” into a role name. JobHop stores application status in the local database and skips listings whose normalized application destination matches one already applied to. Those rows appear as “Duplicate postings skipped” in status and the dashboard. For applications you complete outside JobHop, record the listing with `jobhop apply --mark-applied "<listing URL>"`.

Before a live application, JobHop also checks the saved NYC-area/remote preference. Posts with an unlisted location stay in location review, known out-of-area posts are excluded, and internship/co-op/apprenticeship titles are excluded from this professional-role search. The dry-run preview only exposes browser inspection tools; it cannot click Apply or submit.

To keep a particular listing out of every application run, add its URL on a separate line in `%USERPROFILE%\.jobhop\excluded_urls.txt` (Windows) or `~/.jobhop/excluded_urls.txt` (macOS/Linux). JobHop ignores the URL's query string and checks both the listing and application URLs, including when a listing is selected directly.

## First run

Run the stages that prepare materials, then inspect what JobHop found:

```powershell
jobhop run
jobhop status
```

For a browser preview of one prepared listing:

```powershell
jobhop apply --dry-run --limit 1
```

The dry run opens a clean temporary Chrome profile and asks Codex to inspect the visible application page. It does not submit an application, create an employer account, send email, or mark the job as applied. Start with this mode and inspect the preview before choosing whether to run live applications.

JobHop must use only facts supported by your profile and resume. If a required question is not supported by those facts, the workflow should stop for manual review. Do not add an answer simply to make an application proceed.

For the full command list and pipeline details, see the [README](../README.md). For common setup and discovery problems, see [Troubleshooting](TROUBLESHOOTING.md).
