"""Parse an out-of-office return date from message text. Pure, naive-local."""
from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_NO_DATE = re.compile(r"until further notice|indefinitely", re.IGNORECASE)
_MONTH_NAME = "|".join(_MONTHS)
_RE_MONTH_DAY = re.compile(rf"\b({_MONTH_NAME})\s+(\d{{1,2}})\b", re.IGNORECASE)
_RE_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_NAME})\b", re.IGNORECASE)
_RE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_RE_MDY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_RE_MD = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")


def _roll(year_month_day: tuple[int, int, int] | None, *, today: date, has_year: bool) -> date | None:
    if year_month_day is None:
        return None
    y, m, d = year_month_day
    try:
        candidate = date(y, m, d)
    except ValueError:
        return None
    if not has_year and candidate < today:
        try:
            candidate = date(y + 1, m, d)
        except ValueError:
            return None
    return candidate


def parse_return_date(text: str, *, today: date) -> date | None:
    """Latest plausible return date in `text`, or None. Bare M/D and Month-D roll to the
    next occurrence on/after `today`. 'until further notice' / 'indefinitely' -> None."""
    if _NO_DATE.search(text):
        return None

    found: list[date] = []

    for m in _RE_ISO.finditer(text):
        d = _roll((int(m.group(1)), int(m.group(2)), int(m.group(3))), today=today, has_year=True)
        if d:
            found.append(d)
    for m in _RE_MDY.finditer(text):
        d = _roll((int(m.group(3)), int(m.group(1)), int(m.group(2))), today=today, has_year=True)
        if d:
            found.append(d)
    for m in _RE_MD.finditer(text):
        # skip if this slash-date is actually the M/D/YYYY already captured (overlap): the
        # YYYY group means _RE_MD also matches the leading M/D; dedup by value below.
        d = _roll((today.year, int(m.group(1)), int(m.group(2))), today=today, has_year=False)
        if d:
            found.append(d)
    for m in _RE_MONTH_DAY.finditer(text):
        d = _roll((today.year, _MONTHS[m.group(1).lower()], int(m.group(2))), today=today, has_year=False)
        if d:
            found.append(d)
    for m in _RE_DAY_MONTH.finditer(text):
        d = _roll((today.year, _MONTHS[m.group(2).lower()], int(m.group(1))), today=today, has_year=False)
        if d:
            found.append(d)

    if not found:
        return None
    return max(found)
