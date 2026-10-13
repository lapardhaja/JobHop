# JobHop Changelog

## 0.3.0 — 2026-10-13

### Added

- Codex CLI as the default backend for scoring, resume tailoring, cover letters, and browser-assisted applications.
- A read-only application preview that opens the target page in a clean temporary browser profile and gives Codex restricted page inspection tools.
- A JobHop setup wizard, CLI, dashboard, and local data directory.
- Setup and troubleshooting guides for Codex, Playwright Chromium, local profile data, and city-wide search configuration.

### Changed

- Applicant answers must be supported by the profile or resume; unsupported required questions stop for manual review.
- JobHop uses a standalone Python package, command name, environment variable prefix, database filename, and default data directory.
- Local profile and job data are stored outside the source checkout by default.

### Preserved

- The original project license and attribution notices. See NOTICE.md.
