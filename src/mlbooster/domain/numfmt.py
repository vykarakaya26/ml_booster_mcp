"""Turkish number formatting for user-facing messages (decimal comma, thousands dot).

Code-side values (JSON payloads, enum values) are never formatted with these helpers.
"""

from __future__ import annotations

_SWAP = str.maketrans(",.", ".,")


def tr_num(value: float, spec: str = ".1f") -> str:
    """`format(value, spec)` with Turkish separators: 31.5 → "31,5", 200,000 → "200.000"."""
    return format(value, spec).translate(_SWAP)


def tr_pct(value: float, spec: str = ".1f") -> str:
    """Percentage in Turkish notation: 31.5 → "%31,5"."""
    return "%" + tr_num(value, spec)
