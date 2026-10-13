# Troubleshooting

## `jobhop doctor` reports that Codex is missing or signed out

Make sure the Codex CLI is installed and available in the same shell, then sign in:

```powershell
codex --version
codex login
jobhop doctor
```

JobHop uses Codex by default for AI stages. It does not switch to an API provider just because an API key exists. To use an API provider, select it explicitly in the local `.env` file.

## Chromium is missing during enrichment

JobHop's enrichment stage needs Playwright's Chromium build. Installing Google Chrome alone may not provide it. Activate the project's virtual environment, then run:

```powershell
python -m playwright install chromium
```

Run `jobhop doctor` again. Auto-apply also needs Chrome, Node.js 18+, and `npx`.

## Some job boards return an error or fewer results

Job boards may rate-limit or block automated requests. One board returning a 403 or no results does not necessarily mean other sources failed. Check `jobhop status` for discovered jobs, descriptions, scores, and pending work. A listing without a full description may remain pending enrichment until its page can be read.

## A tailored resume fails validation

JobHop checks that preserved companies, projects, schools, and skills remain supported by the profile. Add every relevant skill to the appropriate `skills_boundary` list in the local `profile.json`. Separate multiple preserved schools with semicolons, especially when a school name itself contains a comma. Do not add a skill or credential unless the resume supports it.

The setup wizard now uses semicolons for lists so punctuation inside company, school, project, and metric names is retained. If an older profile was created with a previous version, review its `resume_facts` and `skills_boundary` values directly.

## The wizard's salary range does not match your preference

Enter ranges as digits with no prose: `80000-120000` for a closed range or `80000+` for an open-ended minimum. Leave the acceptable-range prompt blank if you do not want a separate range. The salary expectation remains a separate value.

## The dry-run preview cannot inspect a listing

Confirm Chrome, Node.js, `npx`, and the local Codex sign-in with `jobhop doctor`. A listing may also be unavailable or require a site-specific login. A preview is read-only; if the page cannot be inspected or an answer is unsupported, handle that listing manually.

## Keep personal data out of Git

User data lives outside the source checkout by default. Before staging changes, check `git status --short` and make sure the profile, resume, `.env`, database, tailored resumes, cover letters, and logs are not listed. The repository's `.gitignore` excludes these runtime files. Do not add real profile information to the example files.
