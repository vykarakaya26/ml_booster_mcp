from datetime import date

import pytest

from mlbooster.domain.model import Frequency
from mlbooster.domain.periods import parse_period


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024-02-10", (date(2024, 2, 10), Frequency.DAILY)),
        ("2024-02-10T00:00:00", (date(2024, 2, 10), Frequency.DAILY)),
        ("2024-2", (date(2024, 2, 29), Frequency.MONTHLY)),
        ("2024-Q1", (date(2024, 3, 31), Frequency.QUARTERLY)),
        ("2024 ç4", (date(2024, 12, 31), Frequency.QUARTERLY)),
        ("2024-S2", (date(2024, 12, 31), Frequency.SEMI_ANNUAL)),
        ("2024-H1", (date(2024, 6, 30), Frequency.SEMI_ANNUAL)),
        (" 2024 ", (date(2024, 12, 31), Frequency.ANNUAL)),
    ],
)
def test_parse_period_maps_to_period_end(raw, expected):
    assert parse_period(raw) == expected


@pytest.mark.parametrize("raw", ["2024-13", "2024-02-30", "24-01", "2024-Q5", "", "Ocak 2024"])
def test_parse_period_returns_none_for_unknown_or_invalid(raw):
    assert parse_period(raw) is None
