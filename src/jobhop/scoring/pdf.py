"""Text-to-PDF conversion for tailored resumes and cover letters.

Parses the structured text resume format, renders via an HTML/CSS template,
and exports to PDF using headless Chromium via Playwright.
"""

import logging
import re
from html import escape
from pathlib import Path

from jobhop.config import TAILORED_DIR

log = logging.getLogger(__name__)


def _is_section_heading(line: str) -> bool:
    stripped = line.strip()
    section_terms = (
        "SUMMARY", "SKILL", "EXPERIENCE", "PROJECT", "EDUCATION", "CERTIFICATION",
        "PUBLICATION", "AWARD", "HONOR", "VOLUNTEER", "ACHIEVEMENT", "LEADERSHIP",
        "ACTIVITIES", "ADDITIONAL",
    )
    return bool(
        len(stripped) > 3
        and stripped == stripped.upper()
        and any(char.isalpha() for char in stripped)
        and any(term in stripped for term in section_terms)
        and not stripped.startswith(("•", "●", "▪", "-"))
        and ":" not in stripped
        and "|" not in stripped
    )


# ── Resume Parser ────────────────────────────────────────────────────────

def parse_resume(text: str) -> dict:
    """Parse a structured text resume into sections.

    Expects a format with header lines (name, title, location, contact)
    followed by ALL-CAPS section headers (SUMMARY, TECHNICAL SKILLS, etc.).

    Args:
        text: Full resume text.

    Returns:
        {"name": str, "title": str, "location": str, "contact": str, "sections": dict}
    """
    lines = [line.rstrip() for line in text.strip().split("\n")]

    # The first recognized uppercase section heading ends the contact header.
    body_start = next((i for i, line in enumerate(lines) if _is_section_heading(line)), len(lines))
    header_lines = [line.strip() for line in lines[:body_start] if line.strip()]

    name = header_lines[0] if len(header_lines) > 0 else ""
    title = header_lines[1] if len(header_lines) > 1 else ""
    # The header may have 3 or 4 lines depending on whether location is included
    location = ""
    contact = ""
    if len(header_lines) > 3:
        location = header_lines[2]
        contact = header_lines[3]
    elif len(header_lines) > 2:
        # Could be location or contact -- check for email/phone indicators
        if "@" in header_lines[2] or "|" in header_lines[2]:
            contact = header_lines[2]
        else:
            location = header_lines[2]

    # Split body into sections by ALL-CAPS headers
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_lines: list[str] = []

    for line in lines[body_start:]:
        stripped = line.strip()
        # Detect section headers (all caps, no leading dash/bullet, longer than 3 chars)
        if _is_section_heading(stripped):
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return {
        "name": name,
        "title": title,
        "location": location,
        "contact": contact,
        "sections": sections,
    }


def parse_skills(text: str) -> list[tuple[str, str]]:
    """Parse skills section into (category, value) pairs.

    Args:
        text: The TECHNICAL SKILLS section text.

    Returns:
        List of (category_name, skills_string) tuples.
    """
    skills: list[tuple[str, str]] = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if ":" in line:
            cat, val = line.split(":", 1)
            skills.append((cat.strip(), val.strip()))
    return skills


def parse_entries(text: str) -> list[dict]:
    """Parse experience/project entries from section text.

    Args:
        text: The EXPERIENCE or PROJECTS section text.

    Returns:
        List of {"title": str, "subtitle": str, "bullets": list[str]} dicts.
    """
    entries: list[dict] = []
    lines = text.strip().split("\n")
    current: dict | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[•●▪-]\s+", stripped):
            if current:
                current["bullets"].append(re.sub(r"^[•●▪-]\s+", "", stripped).strip())
        elif current is None or (
            not re.match(r"^[•●▪-]", stripped)
            and len(current.get("bullets", [])) > 0
        ):
            # New entry
            if current:
                entries.append(current)
            current = {"title": stripped, "subtitle": "", "bullets": []}
        elif current and not current["subtitle"]:
            current["subtitle"] = stripped
        else:
            if current:
                current["bullets"].append(stripped)

    if current:
        entries.append(current)

    return entries


def _format_entry_label(value: str) -> str:
    """Add display-only separators where PDF text extraction joined adjacent columns."""
    month = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    value = re.sub(rf"(?<=[A-Za-z0-9])(?={month}\s+\d{{4}}\b)", " | ", value)
    value = re.sub(r"(?<=[A-Za-z0-9])(?=(?:19|20)\d{2}\b(?:\s|$))", " | ", value)
    value = re.sub(r"(?<=[A-Za-z])(?=(?:Remote|Hybrid|On-site)\b)", " | ", value)
    value = re.sub(
        r"(?<=[A-Za-z])(?=[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s+(?:[A-Z]{2}|[A-Z][a-z]+)\b)",
        " | ", value,
    )
    return re.sub(r"\s{2,}", " ", value).strip()


# ── HTML Template ────────────────────────────────────────────────────────

def build_html(resume: dict) -> str:
    """Render every source section, in order, with escaped text and multi-page flow."""
    def inline_text(value: str) -> str:
        return escape(value, quote=True)

    rendered_sections: list[str] = []
    for heading, content in resume["sections"].items():
        normalized = heading.upper()
        safe_heading = inline_text(heading)
        section_class = "section page-break-before" if normalized == "RESEARCH EXPERIENCE" else "section"
        if "SKILL" in normalized:
            rows = parse_skills(content)
            body = "".join(
                f'<div class="skill-row"><span class="skill-cat">{inline_text(category)}:</span> '
                f'{inline_text(value)}</div>'
                for category, value in rows
            )
            if not body:
                body = f'<div class="plain">{inline_text(content).replace(chr(10), "<br>")}</div>'
        elif "EXPERIENCE" in normalized or normalized == "PROJECTS":
            entries = parse_entries(content)
            body_parts: list[str] = []
            for entry in entries:
                subtitle = (
                    f'<div class="entry-subtitle">{inline_text(_format_entry_label(entry["subtitle"]))}</div>'
                    if entry["subtitle"] else ""
                )
                bullets = "".join(f"<li>{inline_text(bullet)}</li>" for bullet in entry["bullets"])
                body_parts.append(
                    f'<div class="entry"><div class="entry-title">{inline_text(_format_entry_label(entry["title"]))}</div>'
                    f'{subtitle}<ul>{bullets}</ul></div>'
                )
            body = "".join(body_parts) or f'<div class="plain">{inline_text(content).replace(chr(10), "<br>")}</div>'
        else:
            safe_lines = [
                inline_text(_format_entry_label(line.strip()))
                for line in content.splitlines() if line.strip()
            ]
            if "SUMMARY" in normalized:
                body = f'<div class="summary">{"<br>".join(safe_lines)}</div>'
            else:
                body = f'<div class="plain">{"<br>".join(safe_lines)}</div>'
        rendered_sections.append(
            f'<section class="{section_class}"><div class="section-title">{safe_heading}</div>{body}</section>'
        )

    contact = " &nbsp;|&nbsp; ".join(
        inline_text(part.strip()) for part in resume["contact"].split("|") if part.strip()
    )
    location = f'<div class="location">{inline_text(resume["location"])}</div>' if resume["location"] else ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
@page {{ size: letter; margin: 0.28in 0.45in; }}
* {{ box-sizing: border-box; }}
body {{ font-family: Calibri, 'Segoe UI', Arial, sans-serif; font-size: 10.5pt; line-height: 1.25; color: #1a1a1a; margin: 0; }}
.header {{ text-align: center; margin-bottom: 8px; padding-bottom: 5px; border-bottom: 1.5px solid #2a7ab5; }}
.name {{ font-size: 17pt; font-weight: 700; color: #1a3a5c; }}
.title {{ font-size: 10.5pt; color: #3a6b8c; margin: 2px 0; }}
.location, .contact {{ font-size: 9pt; color: #444; }}
.section {{ margin-top: 7px; break-inside: auto; page-break-inside: auto; }}
.page-break-before {{ break-before: page; page-break-before: always; }}
.section-title {{ font-size: 10pt; font-weight: 700; color: #1a3a5c; text-transform: uppercase; letter-spacing: .5px; border-bottom: 1px solid #2a7ab5; padding-bottom: 2px; margin-bottom: 4px; break-after: avoid; page-break-after: avoid; }}
.summary, .plain {{ font-size: 10.3pt; line-height: 1.25; white-space: normal; }}
.skill-row {{ font-size: 10pt; line-height: 1.23; }}
.skill-cat {{ font-weight: 600; color: #1a3a5c; }}
.entry {{ margin-bottom: 6px; break-inside: avoid-page; page-break-inside: avoid; }}
.entry-title {{ font-weight: 600; color: #1a3a5c; break-after: avoid; page-break-after: avoid; }}
.entry-subtitle {{ font-size: 9.5pt; color: #4a7a9b; font-style: italic; }}
ul {{ margin: 2px 0 2px 16px; padding: 0; }}
li {{ font-size: 10.2pt; margin: 0 0 2px; line-height: 1.24; orphans: 2; widows: 2; }}
.plain {{ white-space: normal; }}
</style></head><body>
<header class="header"><div class="name">{inline_text(resume['name'])}</div>
<div class="title">{inline_text(resume['title'])}</div>{location}<div class="contact">{contact}</div></header>
{''.join(rendered_sections)}
</body></html>"""


# ── PDF Renderer ─────────────────────────────────────────────────────────

def render_pdf(html: str, output_path: str) -> None:
    """Render HTML to PDF using Playwright's headless Chromium.

    Args:
        html: Complete HTML string.
        output_path: Path to write the PDF file.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=output_path,
            format="Letter",
            margin={
                "top": "0.28in",
                "right": "0.45in",
                "bottom": "0.28in",
                "left": "0.45in",
            },
            print_background=True,
        )
        browser.close()


# ── Public API ───────────────────────────────────────────────────────────

def convert_to_pdf(
    text_path: Path, output_path: Path | None = None, html_only: bool = False
) -> Path:
    """Convert a text resume/cover letter to PDF.

    Args:
        text_path: Path to the .txt file to convert.
        output_path: Optional override for the output path. Defaults to same
            name with .pdf extension.
        html_only: If True, output HTML instead of PDF.

    Returns:
        Path to the generated PDF (or HTML) file.
    """
    text_path = Path(text_path)
    text = text_path.read_text(encoding="utf-8")
    resume = parse_resume(text)
    html = build_html(resume)

    if html_only:
        out = output_path or text_path.with_suffix(".html")
        out = Path(out)
        out.write_text(html, encoding="utf-8")
        log.info("HTML generated: %s", out)
        return out

    out = output_path or text_path.with_suffix(".pdf")
    out = Path(out)
    render_pdf(html, str(out))
    log.info("PDF generated: %s", out)
    return out


def batch_convert(limit: int = 50) -> int:
    """Convert .txt files in TAILORED_DIR that don't have corresponding PDFs.

    Scans for .txt files (excluding _JOB.txt and _REPORT.json), checks if a
    .pdf with the same stem already exists, and converts any that are missing.

    Args:
        limit: Maximum number of files to convert.

    Returns:
        Number of PDFs generated.
    """
    if not TAILORED_DIR.exists():
        log.warning("Tailored directory does not exist: %s", TAILORED_DIR)
        return 0

    txt_files = sorted(TAILORED_DIR.glob("*.txt"))
    # Exclude _JOB.txt and _CL.txt files from resume conversion
    # (they get their own conversion calls)
    candidates = [
        f for f in txt_files
        if not f.name.endswith("_JOB.txt")
    ]

    # Filter to those without a corresponding PDF
    to_convert: list[Path] = []
    for f in candidates:
        pdf_path = f.with_suffix(".pdf")
        if not pdf_path.exists():
            to_convert.append(f)
        if len(to_convert) >= limit:
            break

    if not to_convert:
        log.info("All text files already have PDFs.")
        return 0

    log.info("Converting %d files to PDF...", len(to_convert))
    converted = 0
    for f in to_convert:
        try:
            convert_to_pdf(f)
            converted += 1
        except Exception as e:
            log.error("Failed to convert %s: %s", f.name, e)

    log.info("Done: %d/%d PDFs generated in %s", converted, len(to_convert), TAILORED_DIR)
    return converted
