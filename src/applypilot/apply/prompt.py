"""Prompt builder for the autonomous job application agent.

Constructs the full instruction prompt that tells Codex CLI how to fill out a
job application form using Playwright MCP tools. All
personal data is loaded from the user's profile -- nothing is hardcoded.
"""

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from applypilot import config

logger = logging.getLogger(__name__)


def _build_profile_summary(profile: dict) -> str:
    """Format the applicant profile section of the prompt.

    Reads all relevant fields from the profile dict and returns a
    human-readable multi-line summary for the agent.
    """
    p = profile
    personal = p["personal"]
    work_auth = p["work_authorization"]
    comp = p["compensation"]
    exp = p.get("experience", {})
    avail = p.get("availability", {})
    eeo = p.get("eeo_voluntary", {})

    lines = [
        f"Name: {personal['full_name']}",
        f"Email: {personal['email']}",
        f"Phone: {personal['phone']}",
    ]

    # Address -- handle optional fields gracefully
    addr_parts = [
        personal.get("address", ""),
        personal.get("city", ""),
        personal.get("province_state", ""),
        personal.get("country", ""),
        personal.get("postal_code", ""),
    ]
    lines.append(f"Address: {', '.join(p for p in addr_parts if p)}")

    if personal.get("linkedin_url"):
        lines.append(f"LinkedIn: {personal['linkedin_url']}")
    if personal.get("github_url"):
        lines.append(f"GitHub: {personal['github_url']}")
    if personal.get("portfolio_url"):
        lines.append(f"Portfolio: {personal['portfolio_url']}")
    if personal.get("website_url"):
        lines.append(f"Website: {personal['website_url']}")

    # Work authorization
    lines.append(f"Work Auth: {work_auth.get('legally_authorized_to_work', 'See profile')}")
    lines.append(f"Sponsorship Needed: {work_auth.get('require_sponsorship', 'See profile')}")
    if work_auth.get("work_permit_type"):
        lines.append(f"Work Permit: {work_auth['work_permit_type']}")

    # Compensation
    currency = comp.get("salary_currency", "USD")
    lines.append(f"Salary Expectation: ${comp['salary_expectation']} {currency}")

    # Experience
    if exp.get("years_of_experience_total"):
        lines.append(f"Years Experience: {exp['years_of_experience_total']}")
    if exp.get("education_level"):
        lines.append(f"Education: {exp['education_level']}")

    # Availability
    lines.append(f"Available: {avail.get('earliest_start_date', 'Immediately')}")

    # Optional screening facts appear only when the profile explicitly
    # supplies them. These facts must never be inferred from defaults.
    screening = p.get("screening", p.get("eligibility", p.get("screening_answers", {}))) or {}
    screening_fields = (
        ("Age 18+", ("age_18_plus", "over_18")),
        ("Background Check", ("background_check_consent", "can_pass_background_check")),
        ("Felony History", ("felony_history", "felony_conviction")),
        ("Previously Worked Here", ("previously_worked_here", "worked_for_company_before")),
        ("How Heard", ("how_heard", "source_of_job_posting")),
    )
    for label, keys in screening_fields:
        fact_sources = (screening, p, personal)
        value = next(
            (source[key] for source in fact_sources for key in keys if source.get(key) not in (None, "")),
            None,
        )
        if value is not None:
            lines.append(f"{label}: {value}")

    # EEO
    lines.append(f"Gender: {eeo.get('gender', 'Decline to self-identify')}")
    lines.append(f"Race: {eeo.get('race_ethnicity', 'Decline to self-identify')}")
    lines.append(f"Veteran: {eeo.get('veteran_status', 'I am not a protected veteran')}")
    lines.append(f"Disability: {eeo.get('disability_status', 'I do not wish to answer')}")

    return "\n".join(lines)


def _build_preview_profile_summary(profile: dict) -> str:
    """Provide comparison facts without applicant contact values or passwords."""
    personal = profile.get("personal", {})
    work_auth = profile.get("work_authorization", {})
    experience = profile.get("experience", {})
    skills = profile.get("skills_boundary", {})
    compensation = profile.get("compensation", {})
    screening = profile.get("screening", profile.get("eligibility", profile.get("screening_answers", {}))) or {}
    lines = [
        "Contact values, account credentials, and document paths are withheld in preview.",
    ]

    location = ", ".join(
        str(value) for value in (
            personal.get("city"), personal.get("province_state"), personal.get("country"),
        ) if value
    )
    if location:
        lines.append(f"Applicant work location: {location}")
    if work_auth.get("legally_authorized_to_work") not in (None, ""):
        lines.append(f"Work authorization fact is in profile: {work_auth['legally_authorized_to_work']}")
    if work_auth.get("require_sponsorship") not in (None, ""):
        lines.append(f"Sponsorship fact is in profile: {work_auth['require_sponsorship']}")
    if work_auth.get("work_permit_type"):
        lines.append(f"Work permit type: {work_auth['work_permit_type']}")
    if experience.get("years_of_experience_total"):
        lines.append(f"Years of experience: {experience['years_of_experience_total']}")
    if experience.get("education_level"):
        lines.append(f"Education level: {experience['education_level']}")
    if experience.get("target_role"):
        lines.append(f"Target role: {experience['target_role']}")

    for group, values in skills.items():
        if isinstance(values, (list, tuple)) and values:
            lines.append(f"{group.replace('_', ' ').title()}: {', '.join(map(str, values))}")
    resume_facts = profile.get("resume_facts", {})
    for key, label in (
        ("preserved_companies", "Resume employers"),
        ("preserved_projects", "Resume projects"),
        ("preserved_school", "Resume school"),
        ("real_metrics", "Resume metrics"),
    ):
        value = resume_facts.get(key)
        if value:
            values = value if isinstance(value, (list, tuple)) else [value]
            lines.append(f"{label}: {', '.join(map(str, values))}")

    screening_keys = (
        "age_18_plus", "over_18", "background_check_consent", "can_pass_background_check",
        "felony_history", "felony_conviction", "previously_worked_here",
        "worked_for_company_before", "how_heard", "source_of_job_posting",
    )
    has_screening_facts = any(
        source.get(key) not in (None, "")
        for source in (screening, profile, personal)
        for key in screening_keys
    )
    lines.append(
        "Profile includes explicit screening facts; values withheld."
        if has_screening_facts else
        "No explicit age, background check, felony, prior-employment, or referral facts in profile."
    )
    if compensation.get("salary_expectation") not in (None, ""):
        lines.append("Salary expectation is in profile; value withheld.")
    if profile.get("eeo_voluntary"):
        lines.append("Voluntary demographic preferences are in profile; values withheld.")
    return "\n".join(lines)


def _build_location_check(profile: dict, search_config: dict) -> str:
    """Build the location eligibility check section of the prompt.

    Uses the accept_patterns from search config to determine which cities
    are acceptable for hybrid/onsite roles.
    """
    personal = profile["personal"]
    location_cfg = search_config.get("location", {})
    accept_patterns = location_cfg.get("accept_patterns", [])
    primary_city = personal.get("city", location_cfg.get("primary", "your city"))

    # Build the list of acceptable cities for hybrid/onsite
    if accept_patterns:
        city_list = ", ".join(accept_patterns)
    else:
        city_list = primary_city

    return f"""== LOCATION CHECK (do this FIRST before any form) ==
Read the job page. Determine the work arrangement. Then decide:
- "Remote" or "work from anywhere" -> ELIGIBLE. Apply.
- "Hybrid" or "onsite" in {city_list} -> ELIGIBLE. Apply.
- "Hybrid" or "onsite" in another city BUT the posting also says "remote OK" or "remote option available" -> ELIGIBLE. Apply.
- "Onsite only" or "hybrid only" in any city outside the list above with NO remote option -> NOT ELIGIBLE. Stop immediately. Output RESULT:FAILED:not_eligible_location
- City is overseas (India, Philippines, Europe, etc.) with no remote option -> NOT ELIGIBLE. Output RESULT:FAILED:not_eligible_location
- Cannot determine location -> Continue applying. If a screening question reveals it's non-local onsite, answer honestly and let the system reject if needed.
Do NOT fill out forms for jobs that are clearly onsite in a non-acceptable location. Check EARLY, save time."""


def _build_salary_section(profile: dict) -> str:
    """Build the salary negotiation instructions.

    Adapts floor, range, and currency from the profile's compensation section.
    """
    comp = profile["compensation"]
    currency = comp.get("salary_currency", "USD")
    floor = comp["salary_expectation"]
    range_min = comp.get("salary_range_min") or floor
    range_max = comp.get("salary_range_max") or ""
    if range_max:
        range_guidance = f'"${range_min}-${range_max} {currency}"'
    else:
        range_guidance = f'"${range_min}+ {currency}"'
    conversion_note = comp.get("currency_conversion_note", "")

    # Compute example hourly rates at 3 salary levels
    try:
        floor_int = int(floor)
        examples = [
            (f"${floor_int // 1000}K", floor_int // 2080),
            (f"${(floor_int + 25000) // 1000}K", (floor_int + 25000) // 2080),
            (f"${(floor_int + 55000) // 1000}K", (floor_int + 55000) // 2080),
        ]
        hourly_line = ", ".join(f"{sal} = ${hr}/hr" for sal, hr in examples)
    except (ValueError, TypeError):
        hourly_line = "Divide annual salary by 2080"

    # Currency conversion guidance
    if conversion_note:
        convert_line = f"Posting is in a different currency? -> {conversion_note}"
    else:
        convert_line = "Posting is in a different currency? -> Target midpoint of their range. Convert if needed."

    return f"""== SALARY (think, don't just copy) ==
${floor} {currency} is the FLOOR. Never go below it. But don't always use it either.

Decision tree:
1. Job posting shows a range (e.g. "$120K-$160K")? -> Answer with the MIDPOINT ($140K).
2. Title says Senior, Staff, Lead, Principal, Architect, or level II/III/IV? -> Minimum $110K {currency}. Use midpoint of posted range if higher.
3. {convert_line}
4. No salary info anywhere? -> Use ${floor} {currency}.
5. Asked for a range? -> Give posted midpoint minus 10% to midpoint plus 10%. No posted range? -> {range_guidance}.
6. Hourly rate? -> Divide your annual answer by 2080. ({hourly_line})"""


def _build_screening_section(profile: dict) -> str:
    """Build the screening questions guidance section."""
    personal = profile["personal"]
    city = personal.get("city", "their city")
    work_auth = profile["work_authorization"]

    return f"""== SCREENING QUESTIONS (be truthful) ==
Answer hard facts from profile.json or the tailored resume only. Never guess, fill in a conventional answer, or claim that a skill can be learned quickly. This includes:
  - Location/relocation: lives in {city}, cannot relocate
  - Work authorization: {work_auth.get('legally_authorized_to_work', 'see profile')}
  - Citizenship, clearance, licenses, certifications: answer from profile only
  - Age eligibility, background checks, criminal history, and prior employment: use an explicit profile fact if present; otherwise the answer is unknown

Skills and tools -> say that the applicant has experience only when the skill or supporting experience appears in profile.json or the tailored resume. A related field or tool does not establish experience with a different one.

If a required answer is unknown or unsupported, do not submit a guessed answer. During a live run, stop and output exactly RESULT:FAILED:manual_review_required. During a dry-run, leave the unknown answer untouched, include the question and missing fact in the preview, and finish with RESULT:DRY_RUN.

Open-ended questions ("Why do you want this role?", "Tell us about yourself", "What interests you?") -> Write 2-3 sentences. Be specific to THIS job. Reference something from the job description. Connect it to a real achievement from the resume. No generic fluff. No "I am passionate about..." -- sound like a real person.

EEO/demographics -> "Decline to self-identify" or "Prefer not to say" for everything."""


def _build_hard_rules(profile: dict) -> str:
    """Build the hard rules section with work auth and name from profile."""
    personal = profile["personal"]
    work_auth = profile["work_authorization"]

    full_name = personal["full_name"]
    preferred_name = personal.get("preferred_name", full_name.split()[0])
    preferred_last = full_name.split()[-1] if " " in full_name else ""
    display_name = f"{preferred_name} {preferred_last}".strip() if preferred_last else preferred_name

    # Build work auth rule dynamically
    auth_info = work_auth.get("legally_authorized_to_work", "")
    sponsorship = work_auth.get("require_sponsorship", "")
    permit_type = work_auth.get("work_permit_type", "")

    work_auth_rule = "Work auth: Answer truthfully from profile."
    if permit_type:
        work_auth_rule = f"Work auth: {permit_type}. Sponsorship needed: {sponsorship}."

    name_rule = f'Name: Legal name = {full_name}.'
    if preferred_name and preferred_name != full_name.split()[0]:
        name_rule += f' Preferred name = {preferred_name}. Use "{display_name}" unless a field specifically says "legal name".'

    return f"""== HARD RULES (never break these) ==
1. Every factual application answer must be supported by profile.json or the tailored resume. Never guess or use an assumed default.
2. Never lie about: citizenship, work authorization, criminal history, education credentials, security clearance, licenses, age eligibility, or prior employment.
3. {work_auth_rule}
4. {name_rule}
5. If a required live-run answer is unsupported, stop and output exactly RESULT:FAILED:manual_review_required. In dry-run, leave it unanswered, explain it in the preview, and output RESULT:DRY_RUN."""


def _build_captcha_section() -> str:
    """Build the CAPTCHA detection and solving instructions.

    Reads the CapSolver API key from environment. The CAPTCHA section
    contains no personal data -- it's the same for every user.
    """
    config.load_env()
    capsolver_key = os.environ.get("CAPSOLVER_API_KEY", "")

    return f"""== CAPTCHA ==
You solve CAPTCHAs via the CapSolver REST API. No browser extension. You control the entire flow.
API key: {capsolver_key or 'NOT CONFIGURED — skip to MANUAL FALLBACK for all CAPTCHAs'}
API base: https://api.capsolver.com

CRITICAL RULE: When ANY CAPTCHA appears (hCaptcha, reCAPTCHA, Turnstile -- regardless of what it looks like visually), you MUST:
1. Run CAPTCHA DETECT to get the type and sitekey
2. Run CAPTCHA SOLVE (createTask -> poll -> inject) with the CapSolver API
3. ONLY go to MANUAL FALLBACK if CapSolver returns errorId > 0
Do NOT skip the API call based on what the CAPTCHA looks like. CapSolver solves CAPTCHAs server-side -- it does NOT need to see or interact with images, puzzles, or games. Even "drag the pipe" or "click all traffic lights" hCaptchas are solved via API token, not visually. ALWAYS try the API first.

--- CAPTCHA DETECT ---
Run this browser_evaluate after every navigation, Apply/Submit/Login click, or when a page feels stuck.
IMPORTANT: Detection order matters. hCaptcha elements also have data-sitekey, so check hCaptcha BEFORE reCAPTCHA.

browser_evaluate function: () => {{{{
  const r = {{}};
  const url = window.location.href;
  // 1. hCaptcha (check FIRST -- hCaptcha uses data-sitekey too)
  const hc = document.querySelector('.h-captcha, [data-hcaptcha-sitekey]');
  if (hc) {{{{
    r.type = 'hcaptcha'; r.sitekey = hc.dataset.sitekey || hc.dataset.hcaptchaSitekey;
  }}}}
  if (!r.type && document.querySelector('script[src*="hcaptcha.com"], iframe[src*="hcaptcha.com"]')) {{{{
    const el = document.querySelector('[data-sitekey]');
    if (el) {{{{ r.type = 'hcaptcha'; r.sitekey = el.dataset.sitekey; }}}}
  }}}}
  // 2. Cloudflare Turnstile
  if (!r.type) {{{{
    const cf = document.querySelector('.cf-turnstile, [data-turnstile-sitekey]');
    if (cf) {{{{
      r.type = 'turnstile'; r.sitekey = cf.dataset.sitekey || cf.dataset.turnstileSitekey;
      if (cf.dataset.action) r.action = cf.dataset.action;
      if (cf.dataset.cdata) r.cdata = cf.dataset.cdata;
    }}}}
  }}}}
  if (!r.type && document.querySelector('script[src*="challenges.cloudflare.com"]')) {{{{
    r.type = 'turnstile_script_only'; r.note = 'Wait 3s and re-detect.';
  }}}}
  // 3. reCAPTCHA v3 (invisible, loaded via render= param)
  if (!r.type) {{{{
    const s = document.querySelector('script[src*="recaptcha"][src*="render="]');
    if (s) {{{{
      const m = s.src.match(/render=([^&]+)/);
      if (m && m[1] !== 'explicit') {{{{ r.type = 'recaptchav3'; r.sitekey = m[1]; }}}}
    }}}}
  }}}}
  // 4. reCAPTCHA v2 (checkbox or invisible)
  if (!r.type) {{{{
    const rc = document.querySelector('.g-recaptcha');
    if (rc) {{{{ r.type = 'recaptchav2'; r.sitekey = rc.dataset.sitekey; }}}}
  }}}}
  if (!r.type && document.querySelector('script[src*="recaptcha"]')) {{{{
    const el = document.querySelector('[data-sitekey]');
    if (el) {{{{ r.type = 'recaptchav2'; r.sitekey = el.dataset.sitekey; }}}}
  }}}}
  // 5. FunCaptcha (Arkose Labs)
  if (!r.type) {{{{
    const fc = document.querySelector('#FunCaptcha, [data-pkey], .funcaptcha');
    if (fc) {{{{ r.type = 'funcaptcha'; r.sitekey = fc.dataset.pkey; }}}}
  }}}}
  if (!r.type && document.querySelector('script[src*="arkoselabs"], script[src*="funcaptcha"]')) {{{{
    const el = document.querySelector('[data-pkey]');
    if (el) {{{{ r.type = 'funcaptcha'; r.sitekey = el.dataset.pkey; }}}}
  }}}}
  if (r.type) {{{{ r.url = url; return r; }}}}
  return null;
}}}}

Result actions:
- null -> no CAPTCHA. Continue normally.
- "turnstile_script_only" -> browser_wait_for time: 3, re-run detect.
- Any other type -> proceed to CAPTCHA SOLVE below.

--- CAPTCHA SOLVE ---
Three steps: createTask -> poll -> inject. Do each as a separate browser_evaluate call.

STEP 1 -- CREATE TASK (copy this exactly, fill in the 3 placeholders):
browser_evaluate function: async () => {{{{
  const r = await fetch('https://api.capsolver.com/createTask', {{{{
    method: 'POST',
    headers: {{{{'Content-Type': 'application/json'}}}},
    body: JSON.stringify({{{{
      clientKey: '{capsolver_key}',
      task: {{{{
        type: 'TASK_TYPE',
        websiteURL: 'PAGE_URL',
        websiteKey: 'SITE_KEY'
      }}}}
    }}}})
  }}}});
  return await r.json();
}}}}

TASK_TYPE values (use EXACTLY these strings):
  hcaptcha     -> HCaptchaTaskProxyLess
  recaptchav2  -> ReCaptchaV2TaskProxyLess
  recaptchav3  -> ReCaptchaV3TaskProxyLess
  turnstile    -> AntiTurnstileTaskProxyLess
  funcaptcha   -> FunCaptchaTaskProxyLess

PAGE_URL = the url from detect result. SITE_KEY = the sitekey from detect result.
For recaptchav3: add "pageAction": "submit" to the task object (or the actual action found in page scripts).
For turnstile: add "metadata": {{"action": "...", "cdata": "..."}} if those were in detect result.

Response: {{"errorId": 0, "taskId": "abc123"}} on success.
If errorId > 0 -> CAPTCHA SOLVE failed. Go to MANUAL FALLBACK.

STEP 2 -- POLL (replace TASK_ID with the taskId from step 1):
Loop: browser_wait_for time: 3, then run:
browser_evaluate function: async () => {{{{
  const r = await fetch('https://api.capsolver.com/getTaskResult', {{{{
    method: 'POST',
    headers: {{{{'Content-Type': 'application/json'}}}},
    body: JSON.stringify({{{{
      clientKey: '{capsolver_key}',
      taskId: 'TASK_ID'
    }}}})
  }}}});
  return await r.json();
}}}}

- status "processing" -> wait 3s, poll again. Max 10 polls (30s).
- status "ready" -> extract token:
    reCAPTCHA: solution.gRecaptchaResponse
    hCaptcha:  solution.gRecaptchaResponse
    Turnstile: solution.token
- errorId > 0 or 30s timeout -> MANUAL FALLBACK.

STEP 3 -- INJECT TOKEN (replace THE_TOKEN with actual token string):

For reCAPTCHA v2/v3:
browser_evaluate function: () => {{{{
  const token = 'THE_TOKEN';
  document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{{{ el.value = token; el.style.display = 'block'; }}}});
  if (window.___grecaptcha_cfg) {{{{
    const clients = window.___grecaptcha_cfg.clients;
    for (const key in clients) {{{{
      const walk = (obj, d) => {{{{
        if (d > 4 || !obj) return;
        for (const k in obj) {{{{
          if (typeof obj[k] === 'function' && k.length < 3) try {{{{ obj[k](token); }}}} catch(e) {{{{}}}}
          else if (typeof obj[k] === 'object') walk(obj[k], d+1);
        }}}}
      }}}};
      walk(clients[key], 0);
    }}}}
  }}}}
  return 'injected';
}}}}

For hCaptcha:
browser_evaluate function: () => {{{{
  const token = 'THE_TOKEN';
  const ta = document.querySelector('[name="h-captcha-response"], textarea[name*="hcaptcha"]');
  if (ta) ta.value = token;
  document.querySelectorAll('iframe[data-hcaptcha-response]').forEach(f => f.setAttribute('data-hcaptcha-response', token));
  const cb = document.querySelector('[data-hcaptcha-widget-id]');
  if (cb && window.hcaptcha) try {{{{ window.hcaptcha.getResponse(cb.dataset.hcaptchaWidgetId); }}}} catch(e) {{{{}}}}
  return 'injected';
}}}}

For Turnstile:
browser_evaluate function: () => {{{{
  const token = 'THE_TOKEN';
  const inp = document.querySelector('[name="cf-turnstile-response"], input[name*="turnstile"]');
  if (inp) inp.value = token;
  if (window.turnstile) try {{{{ const w = document.querySelector('.cf-turnstile'); if (w) window.turnstile.getResponse(w); }}}} catch(e) {{{{}}}}
  return 'injected';
}}}}

For FunCaptcha:
browser_evaluate function: () => {{{{
  const token = 'THE_TOKEN';
  const inp = document.querySelector('#FunCaptcha-Token, input[name="fc-token"]');
  if (inp) inp.value = token;
  if (window.ArkoseEnforcement) try {{{{ window.ArkoseEnforcement.setConfig({{{{data: {{{{blob: token}}}}}}}}) }}}} catch(e) {{{{}}}}
  return 'injected';
}}}}

After injecting: browser_wait_for time: 2, then snapshot.
- Widget gone or green check -> success. Click Submit if needed.
- No change -> click Submit/Verify/Continue button (some sites need it).
- Still stuck -> token may have expired (~2 min lifetime). Re-run from STEP 1.

--- MANUAL FALLBACK ---
You should ONLY be here if CapSolver createTask returned errorId > 0. If you haven't tried CapSolver yet, GO BACK and try it first.
If CapSolver genuinely failed (errorId > 0):
1. Audio challenge: Look for "audio" or "accessibility" button -> click it for an easier challenge.
2. Text/logic puzzles: Solve them yourself. Think step by step. Common tricks: "All but 9 die" = 9 left. "3 sisters and 4 brothers, how many siblings?" = 7.
3. Simple text captchas ("What is 3+7?", "Type the word") -> solve them.
4. All else fails -> Output RESULT:CAPTCHA."""


def build_prompt(job: dict, tailored_resume: str,
                 cover_letter: str | None = None,
                 dry_run: bool = False) -> str:
    """Build the full instruction prompt for the apply agent.

    Loads the user profile and search config internally. All personal data
    comes from the profile -- nothing is hardcoded.

    Args:
        job: Job dict from the database (must have url, title, site,
             application_url, fit_score, tailored_resume_path).
        tailored_resume: Plain-text content of the tailored resume.
        cover_letter: Optional plain-text cover letter content.
        dry_run: If True, tell the agent to preview without submitting, emailing, or creating an account.

    Returns:
        Complete prompt string for the AI agent.
    """
    profile = config.load_profile()
    search_config = config.load_search_config()
    personal = profile["personal"]

    # --- Resolve application materials ---
    resume_path = job.get("tailored_resume_path")
    pdf_path = ""
    dest_dir = config.APPLY_WORKER_DIR / "current"
    if not dry_run:
        if not resume_path:
            raise ValueError(f"No tailored resume for job: {job.get('title', 'unknown')}")
        src_pdf = Path(resume_path).with_suffix(".pdf").resolve()
        if not src_pdf.exists():
            raise ValueError(f"Resume PDF not found: {src_pdf}")

        # Copy to a clean filename for upload (recruiters see the filename).
        full_name = personal["full_name"]
        name_slug = full_name.replace(" ", "_")
        dest_dir.mkdir(parents=True, exist_ok=True)
        upload_pdf = dest_dir / f"{name_slug}_Resume.pdf"
        shutil.copy(str(src_pdf), str(upload_pdf))
        pdf_path = str(upload_pdf)

    # --- Cover letter handling ---
    cover_letter_text = "" if dry_run else (cover_letter or "")
    cl_upload_path = ""
    cl_path = job.get("cover_letter_path")
    if not dry_run and cl_path and Path(cl_path).exists():
        cl_src = Path(cl_path)
        # Read text from .txt sibling (PDF is binary)
        cl_txt = cl_src.with_suffix(".txt")
        if cl_txt.exists():
            cover_letter_text = cl_txt.read_text(encoding="utf-8")
        elif cl_src.suffix == ".txt":
            cover_letter_text = cl_src.read_text(encoding="utf-8")
        # Upload must be PDF
        cl_pdf_src = cl_src.with_suffix(".pdf")
        if cl_pdf_src.exists():
            cl_upload = dest_dir / f"{name_slug}_Cover_Letter.pdf"
            shutil.copy(str(cl_pdf_src), str(cl_upload))
            cl_upload_path = str(cl_upload)

    # --- Build all prompt sections ---
    profile_summary = (
        _build_preview_profile_summary(profile) if dry_run else _build_profile_summary(profile)
    )
    hard_rules = (
        "Never infer applicant facts from defaults. Report whether each visible question has "
        "support in the provided structured profile and resume facts. Do not provide, repeat, "
        "or send personal contact details, credentials, or document paths."
        if dry_run else _build_hard_rules(profile)
    )

    # Cover letter fallback text
    city = personal.get("city", "the area")
    if dry_run:
        cl_display = "Cover letter content is withheld in read-only preview."
    elif not cover_letter_text:
        cl_display = (
            f"None available. Skip if optional. If required, write 2 factual "
            f"sentences: (1) relevant experience from the resume that matches "
            f"this role, (2) available immediately and based in {city}."
        )
    else:
        cl_display = cover_letter_text

    # Phone digits only (for fields with country prefix)
    phone_digits = "".join(c for c in personal.get("phone", "") if c.isdigit())

    # SSO domains the agent cannot sign into (loaded from config/sites.yaml)
    from applypilot.config import load_blocked_sso
    blocked_sso = load_blocked_sso()

    # Preferred display name
    # Dry-run: preview only, with no submission, email, or account creation.
    dry_run_guard = (
        "\n\nDRY-RUN SAFETY OVERRIDE: This is a read-only preview. Do not click, type, fill, "
        "upload, select, press keys, evaluate page code, make network requests, activate "
        "a Submit/Apply/Send/Confirm control, send email, or create an account. The "
        "invocation exposes only browser inspection tools and disables the Gmail server. These rules "
        "override any later instruction that conflicts with them. Inspect the current "
        "page only and finish with RESULT:DRY_RUN."
        if dry_run else ""
    )
    mission = (
        "Inspect the current application page and report what is visible and which "
        "answers are supported by the provided profile and resume. Do not change page state."
        if dry_run else
        "Submit a complete, accurate application. Use the profile and resume as source data -- adapt to fit each form's format."
    )

    if dry_run:
        workflow_sections = f"""== READ-ONLY PREVIEW RULES ==
{hard_rules}
Inspect only the page already opened at the provided job URL. Do not navigate to another URL. Report the visible work location and arrangement, application fields, and screening questions. For each question, say whether the supplied structured profile and resume facts support an answer; include the missing fact for anything unsupported. Do not enter any answer or alter the page.
If the page requires login, account creation, a CAPTCHA, navigation to another application step, or any action beyond opening the job URL and reading the page, stop and report that limitation. Finish with exactly RESULT:DRY_RUN.

== READ-ONLY PREVIEW WORKFLOW ==
1. Inspect the already-open page with a snapshot. The preview browser was opened at the provided job URL before Codex started.
2. Report the job title, employer, visible location/work arrangement, and whether the page appears to be an application form.
3. List visible application and screening questions. For each, report supported by profile/resume or identify the missing fact.
4. If the page is closed, broken, asks for login, shows a CAPTCHA, or requires any browser action beyond reading, explain that and stop.
5. Do not click, type, fill, upload, select, press keys, evaluate page code, make network requests, send email, create an account, solve a CAPTCHA, or submit anything.
6. Finish with exactly RESULT:DRY_RUN. Never output RESULT:APPLIED.

Only the inspection tools are available in this preview; local shell/file access, native browser and computer-use, connected apps/plugins, code mode, and subagent tools are disabled. Ignore all live-application instructions, including any instructions elsewhere about navigation, filling forms, CAPTCHA handling, or submitting applications."""
        captcha_section = ""
    else:
        submit_instruction = "BEFORE clicking Submit/Apply, take a snapshot and review EVERY field on the page. Verify all data matches the APPLICANT PROFILE and TAILORED RESUME -- name, email, phone, location, work auth, resume uploaded, cover letter if applicable. If anything is wrong or missing, fix it FIRST. Only click Submit after confirming everything is correct."
        location_check = _build_location_check(profile, search_config)
        salary_section = _build_salary_section(profile)
        screening_section = _build_screening_section(profile)
        captcha_section = _build_captcha_section()
        workflow_sections = f"""{hard_rules}

== NEVER DO THESE (immediate RESULT:FAILED if encountered) ==
- NEVER grant camera, microphone, screen sharing, or location permissions. If a site requests them -> RESULT:FAILED:unsafe_permissions
- NEVER do video/audio verification, selfie capture, ID photo upload, or biometric anything -> RESULT:FAILED:unsafe_verification
- NEVER set up a freelancing profile (Mercor, Toptal, Upwork, Fiverr, Turing, etc.). These are contractor marketplaces, not job applications -> RESULT:FAILED:not_a_job_application
- NEVER agree to hourly/contract rates, availability calendars, or "set your rate" flows. You are applying for FULL-TIME salaried positions only.
- NEVER install browser extensions, download executables, or run assessment software.
- NEVER enter payment info, bank details, or SSN/SIN.
- NEVER click "Allow" on any browser permission popup. Always deny/block.
- If the site is NOT a job application form (it's a profile builder, skills marketplace, talent network signup, coding assessment platform) -> RESULT:FAILED:not_a_job_application

{location_check}

{salary_section}

{screening_section}

== STEP-BY-STEP ==
1. browser_navigate to the job URL.
2. browser_snapshot to read the page. Then run CAPTCHA DETECT (see CAPTCHA section). If a CAPTCHA is found, solve it before continuing.
3. LOCATION CHECK. Read the page for location info. If not eligible, output RESULT and stop.
4. Find and click the Apply button. If email-only (page says "email resume to X"):
   - During a live run, send the application email with the resume attached, then output RESULT:APPLIED.
   After clicking Apply: browser_snapshot. Run CAPTCHA DETECT -- many sites trigger CAPTCHAs right after the Apply click. If found, solve before continuing.
5. Login wall?
   5a. FIRST: check the URL. If you landed on {', '.join(blocked_sso)}, or any SSO/OAuth page -> STOP. Output RESULT:FAILED:sso_required. Do NOT try to sign in to Google/Microsoft/SSO.
   5b. Check for popups. Run browser_tabs action "list". If a new tab/window appeared (login popup), switch to it with browser_tabs action "select". Check the URL there too -- if it's SSO -> RESULT:FAILED:sso_required.
   5c. Regular login form (employer's own site)? Try sign in: {personal['email']} / {personal.get('password', '')}
   5d. After clicking Login/Sign-in: run CAPTCHA DETECT. Login pages frequently have invisible CAPTCHAs that silently block form submissions. If found, solve it then retry login.
   5e. Sign in failed? Try sign up with the same email and password.
   5f. Need email verification? Use search_emails + read_email to get the code.
   5g. After login, run browser_tabs action "list" again. Switch back to the application tab if needed.
   5h. All failed? Output RESULT:FAILED:login_issue. Do not loop.
6. Upload resume. ALWAYS upload fresh -- delete any existing resume first, then browser_file_upload with the PDF path above. This is the tailored resume for THIS job. Non-negotiable.
7. Upload cover letter if there's a field for it. Text field -> paste the cover letter text. File upload -> use the cover letter PDF path.
8. Check ALL pre-filled fields. ATS systems parse your resume and auto-fill -- it's often WRONG.
   - "Current Job Title" or "Most Recent Title" -> use the title from the TAILORED RESUME summary, NOT whatever the parser guessed.
   - Compare every other field to the APPLICANT PROFILE. Fix mismatches. Fill empty fields.
9. Answer screening questions using the rules above.
10. {submit_instruction}
11. After a live submission only: browser_snapshot. Run CAPTCHA DETECT -- submit buttons often trigger invisible CAPTCHAs. If found, solve it (the form may auto-submit once the token clears). Then check for new tabs (browser_tabs action: "list"). Switch to newest, close old. Snapshot to confirm submission. Look for "thank you" or "application received".
12. Output your result.

== BROWSER EFFICIENCY ==
- browser_snapshot ONCE per page to understand it. Then use browser_take_screenshot to check results (10x less memory).
- Only snapshot again when you need element refs to click/fill.
- Multi-page forms (Workday, Taleo, iCIMS): snapshot each new page, fill all fields, click Next/Continue. Repeat until final review page.
- Fill ALL fields in ONE browser_fill_form call. Not one at a time.
- Keep your thinking SHORT. Don't repeat page structure back.
- CAPTCHA AWARENESS: After any navigation, Apply/Submit/Login click, or when a page feels stuck -- run CAPTCHA DETECT (see CAPTCHA section). Invisible CAPTCHAs (Turnstile, reCAPTCHA v3) show NO visual widget but block form submissions silently. The detect script finds them even when invisible.

== FORM TRICKS ==
- Popup/new window opened? browser_tabs action "list" to see all tabs. browser_tabs action "select" with the tab index to switch. ALWAYS check for new tabs after clicking login/apply/sign-in buttons.
- "Upload your resume" pre-fill page (Workday, Lever, etc.): This is NOT the application form yet. Click "Select file" or the upload area, then browser_file_upload with the resume PDF path. Wait for parsing to finish. Then click Next/Continue to reach the actual form.
- File upload not working? Try: (1) browser_click the upload button/area, (2) browser_file_upload with the path. If still failing, look for a hidden file input or a "Select file" link and click that first.
- Dropdown won't fill? browser_click to open it, then browser_click the option.
- Checkbox won't check via fill_form? Use browser_click on it instead. Snapshot to verify.
- Phone field with country prefix: just type digits {phone_digits}
- Date fields: {datetime.now().strftime('%m/%d/%Y')}
- Validation errors after submit? Take BOTH snapshot AND screenshot. Snapshot shows text errors, screenshot shows red-highlighted fields. Fix all, retry.
- Honeypot fields (hidden, "leave blank"): skip them.
- Format-sensitive fields: read the placeholder text, match it exactly.

{captcha_section}

== WHEN TO GIVE UP ==
- Same page after 3 attempts with no progress -> RESULT:FAILED:stuck
- Job is closed/expired/page says "no longer accepting" -> RESULT:EXPIRED
- Page is broken/500 error/blank -> RESULT:FAILED:page_error
Stop immediately. Output your RESULT code. Do not loop."""

    if dry_run:
        materials_section = """== RESUME MATERIALS ==
Raw resume and cover-letter text, PDFs, contact details, credentials, and document paths are withheld. The structured professional facts above are the only applicant information available for comparison."""
    else:
        materials_section = f"""== FILES ==
Resume PDF (upload this): {pdf_path}
Cover Letter PDF (upload if asked): {cl_upload_path or "N/A"}

== RESUME TEXT (use when filling text fields) ==
{tailored_resume}

== COVER LETTER TEXT (paste if text field, upload PDF if file field) ==
{cl_display}"""

    job_url = job.get("application_url") or job["url"]
    if dry_run:
        parsed_job_url = urlsplit(job_url)
        job_url = urlunsplit((parsed_job_url.scheme, parsed_job_url.netloc, parsed_job_url.path, "", ""))

    prompt = f"""You are a Codex CLI job application agent using the worker's scoped Playwright MCP tools. Follow the mission below using only the information provided. For a live run, submit only after the final review instructions. For a dry-run, never submit.{dry_run_guard}

== JOB ==
URL: {job_url}
Title: {job['title']}
Company: {job.get('site', 'Unknown')}
Fit Score: {job.get('fit_score', 'N/A')}/10

{materials_section}

== APPLICANT PROFILE ==
{profile_summary}

== YOUR MISSION ==
{mission}

If something unexpected happens, stay within this employer's application flow and the listed safeguards. Do not take unrelated actions or invent applicant information.

{workflow_sections}

== RESULT CODES (output EXACTLY one) ==
RESULT:APPLIED -- submitted successfully in a live run
RESULT:DRY_RUN -- preview completed without submission, email, account creation, or application-status changes
RESULT:EXPIRED -- job closed or no longer accepting applications
RESULT:CAPTCHA -- blocked by unsolvable captcha
RESULT:LOGIN_ISSUE -- could not sign in or create account
RESULT:FAILED:not_eligible_location -- onsite outside acceptable area, no remote option
RESULT:FAILED:not_eligible_work_auth -- requires unauthorized work location
RESULT:FAILED:manual_review_required -- required answer is unsupported by profile.json or tailored resume
RESULT:FAILED:reason -- any other failure (brief reason)"""

    return prompt
