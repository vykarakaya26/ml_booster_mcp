"""Single parser for period strings (`YYYY-MM-DD`, `YYYY-MM`, `YYYY-Qn`, `YYYY-Sn`/`YYYY-Hn`, `YYYY`).

Every period maps to its period-end date (the common calendar is period end). Callers raise their own error type
when the value is not recognised: providers raise `UnexpectedResponse`, user input raises `InvalidInput`.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from mlbooster.domain.model import Frequency

_DAY = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ][0-9:.+\-Z]*)?$")
_MONTH = re.compile(r"^(\d{4})-(\d{1,2})$")
_QUARTER = re.compile(r"^(\d{4})[- ]?[QÇ]([1-4])$", re.IGNORECASE)  # "Ç" = çeyrek (Turkish input)
_HALF = re.compile(r"^(\d{4})-[SH]([12])$", re.IGNORECASE)
_YEAR = re.compile(r"^(\d{4})$")


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_period(raw: str) -> tuple[date, Frequency] | None:
    """Returns (period-end date, frequency implied by the format), or None if the format is not recognised."""
    s = str(raw).strip()
    try:
        if m := _DAY.match(s):
            return date(int(m[1]), int(m[2]), int(m[3])), Frequency.DAILY
        if m := _MONTH.match(s):
            return month_end(int(m[1]), int(m[2])), Frequency.MONTHLY
        if m := _QUARTER.match(s):
            return month_end(int(m[1]), int(m[2]) * 3), Frequency.QUARTERLY
        if m := _HALF.match(s):
            return month_end(int(m[1]), int(m[2]) * 6), Frequency.SEMI_ANNUAL
        if m := _YEAR.match(s):
            return date(int(m[1]), 12, 31), Frequency.ANNUAL
    except ValueError:
        pass  # e.g. month 13 or day 32
    return None
