# Contributing to JobHop

Thanks for helping improve JobHop. Contributions should support a local-first workflow, protect applicant data, and keep application answers grounded in the user's resume and profile.

## Development setup

Requirements: Python 3.11 or newer and Git.

    python -m venv .venv
    # Windows PowerShell: .\.venv\Scripts\Activate.ps1
    # macOS/Linux:        source .venv/bin/activate
    python -m pip install -e ".[dev]"
    python -m playwright install chromium

Check the installed command with:

    jobhop --version
    jobhop --help

## Project structure

    src/jobhop/          Python package and CLI
    src/jobhop/apply/    Browser-assisted application flow
    src/jobhop/config/   Shipped YAML configuration
    src/jobhop/discovery/  Job discovery sources
    src/jobhop/scoring/  Fit scoring and application materials
    docs/                User guides

## Contribution guidelines

- Keep profile data, resumes, credentials, and application history out of Git.
- Preserve truthful-answer checks. Do not invent applicant details to complete a form.
- Add or update tests when changing behavior.
- Update the user guides and changelog when commands or configuration change.
- Keep changes focused and document user-visible behavior.

Before opening a pull request, review git status --short and ensure it contains no personal files or secrets.

## License

Contributions are distributed under the [GNU Affero General Public License v3.0](LICENSE).
