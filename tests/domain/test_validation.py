from datetime import date, timedelta

import pytest

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.validation import MAX_SERIES_PER_REQUEST, DateRange, series_code, series_codes, slug


@pytest.mark.parametrize("code", ["TP.DK.USD.A.YTL", "TP.FG.J0", "bie_dkdovizgn", "TP.APIFON4"])
def test_valid_series_codes(code):
    assert series_code(code) == code


@pytest.mark.parametrize(
    "code",
    [
        "TP.FG.J0&type=xml",  # path parametre enjeksiyonu
        "../etc/passwd",
        "TP.FG.J0?x=1",
        "TP FG",
        "TP.FG.J0#frag",
        "TP%2EFG",
        "",
        "-TP",
        "A" * 65,
        None,
    ],
)
def test_injection_attempts_rejected(code):
    with pytest.raises(InvalidInput):
        series_code(code)


def test_series_codes_limits_and_dedupes():
    assert series_codes(["A", "A", "B"]) == ("A", "B")
    with pytest.raises(InvalidInput):
        series_codes([])
    with pytest.raises(InvalidInput):
        series_codes([f"S{i}" for i in range(MAX_SERIES_PER_REQUEST + 1)])


@pytest.mark.parametrize("value", ["../x", "Bankacilik", "a/b", "a" * 33, ""])
def test_slug_rejects_path_like_values(value):
    with pytest.raises(InvalidInput):
        slug(value)


def test_date_range_rules():
    DateRange(date(2020, 1, 1), date(2021, 1, 1))
    with pytest.raises(InvalidInput):
        DateRange(date(2021, 1, 1), date(2020, 1, 1))
    with pytest.raises(InvalidInput):
        DateRange(date(1900, 1, 1), date(2020, 1, 1))
    with pytest.raises(InvalidInput):
        DateRange(date(2020, 1, 1), date.today() + timedelta(days=400))
