"""Hard eligibility checks for roles and user-selected work locations."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LocationAssessment:
    status: str  # "eligible", "outside", or "unknown"
    reason: str


_REMOTE_LOCATION_MARKERS = (
    "remote", "anywhere", "work from home", "wfh", "distributed",
)
_REMOTE_DESCRIPTION_RE = re.compile(
    r"\b(?:fully\s+remote|100%\s+remote|remote[- ]first|work from anywhere|"
    r"remote anywhere|remote eligible|remote work (?:is )?(?:available|an option)|"
    r"can be performed remotely|remote position|remote role)\b",
    re.IGNORECASE,
)
_STUDENT_ROLE_RE = re.compile(
    r"\b(?:intern(?:ship)?|co[\s-]?op|apprentice)\b",
    re.IGNORECASE,
)


def assess_location(
    location: str | None,
    description: str | None,
    search_config: dict,
) -> LocationAssessment:
    """Compare a posting with the saved allowed locations.

    A missing location remains reviewable, but it is not eligible for a live
    application until enrichment or the posting itself makes it clear.
    """
    location_text = (location or "").strip().casefold()
    description_text = description or ""
    location_cfg = search_config.get("location", {})
    accepted = search_config.get("location_accept") or location_cfg.get("accept_patterns", [])
    rejected = search_config.get("location_reject_non_remote", [])

    if any(marker in location_text for marker in _REMOTE_LOCATION_MARKERS):
        return LocationAssessment("eligible", "The listing is explicitly remote.")

    if _REMOTE_DESCRIPTION_RE.search(description_text):
        return LocationAssessment("eligible", "The description explicitly permits remote work.")

    if not location_text:
        return LocationAssessment("unknown", "The listing does not provide a location or remote status.")

    if any(pattern.casefold() in location_text for pattern in rejected):
        return LocationAssessment("outside", "The listing is in a saved excluded location.")

    if any(pattern.casefold() in location_text for pattern in accepted):
        return LocationAssessment("eligible", "The listing matches a saved NYC-area location.")

    return LocationAssessment("outside", "The listing is outside the saved NYC-area locations.")


def is_student_role(title: str | None) -> bool:
    """Return whether a job title indicates internship, co-op, or apprenticeship work."""
    return bool(_STUDENT_ROLE_RE.search(title or ""))
