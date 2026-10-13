"""Salary parsing and floor eligibility for JobHop listings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

ANNUAL_WORK_HOURS = Decimal("2080")

_AMOUNT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:USD\s*)?(?:US\$|\$)?\s*"
    r"(?P<amount>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<scale>[kKmM])?(?![A-Za-z])"
)
_FOREIGN_CURRENCY_RE = re.compile(
    r"€|£|¥|\b(?:CAD|AUD|EUR|GBP|INR|JPY|CNY|CHF|NZD)\b|(?:CA|C|A)\$",
    re.IGNORECASE,
)
_HOURLY_RE = re.compile(
    r"(?:\b(?:hourly|per\s+hour|an\s+hour|hr\s+rate)\b|/\s*(?:hour|hr)\b)",
    re.IGNORECASE,
)
_NON_ANNUAL_INTERVAL_RE = re.compile(
    r"\b(?:per\s+(?:week|month|day)|weekly|biweekly|monthly|daily|/\s*(?:week|month|day))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SalaryAssessment:
    """A conservative comparison of posted compensation against an annual floor."""

    status: str  # "meets_floor", "below_floor", or "unknown"
    annual_max: Decimal | None
    reason: str


def _amounts(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    for match in _AMOUNT_RE.finditer(text):
        try:
            value = Decimal(match.group("amount").replace(",", ""))
        except InvalidOperation:
            continue
        scale = (match.group("scale") or "").lower()
        if scale == "k":
            value *= Decimal("1000")
        elif scale == "m":
            value *= Decimal("1000000")
        values.append(value)
    return values


def get_salary_floor(profile: dict | None) -> Decimal | None:
    """Read the annual minimum from the saved profile, if it is valid."""
    compensation = (profile or {}).get("compensation") or {}
    raw = compensation.get("salary_expectation") or compensation.get("salary_range_min")
    if raw in (None, ""):
        return None
    values = _amounts(str(raw))
    if not values:
        return None
    floor = values[0]
    return floor if floor > 0 else None


def format_money(value: Decimal | None, currency: str = "USD") -> str:
    """Format an amount for dashboard and prompt text."""
    if value is None:
        return ""
    symbol = "$" if currency.upper() == "USD" else f"{currency.upper()} "
    if value == value.to_integral_value():
        return f"{symbol}{value:,.0f}"
    return f"{symbol}{value.quantize(Decimal('0.01')):,.2f}"


def assess_salary(salary: str | None, floor: Decimal | None) -> SalaryAssessment:
    """Classify posted salary without guessing at unclear currencies or periods.

    Hourly rates are annualized at 2,080 hours. A range is eligible when its
    posted maximum reaches the floor; the application prompt separately
    requires any entered expectation to remain at or above the floor.
    """
    if floor is None or not salary or not salary.strip():
        return SalaryAssessment("unknown", None, "Pay is missing or the profile floor is unavailable.")

    text = salary.strip()
    if _FOREIGN_CURRENCY_RE.search(text):
        return SalaryAssessment("unknown", None, "The posted currency needs review before comparison.")
    if _NON_ANNUAL_INTERVAL_RE.search(text) and not _HOURLY_RE.search(text):
        return SalaryAssessment("unknown", None, "The posted pay period is not annual or hourly.")

    values = _amounts(text)
    if not values:
        return SalaryAssessment("unknown", None, "The posted compensation could not be read reliably.")

    maximum = max(values)
    if _HOURLY_RE.search(text):
        maximum *= ANNUAL_WORK_HOURS
    elif maximum < Decimal("1000"):
        return SalaryAssessment("unknown", None, "The compensation amount has no clear pay period.")

    if maximum < floor:
        return SalaryAssessment(
            "below_floor",
            maximum,
            f"Posted maximum {format_money(maximum)} annualized is below the {format_money(floor)} floor.",
        )
    return SalaryAssessment(
        "meets_floor",
        maximum,
        f"Posted maximum reaches the {format_money(floor)} floor.",
    )
