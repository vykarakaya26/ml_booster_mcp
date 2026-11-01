"""Value objects for identifiers and date ranges coming from the outside world.

Series codes are appended to provider URLs (to the path, for EVDS) without encoding. Therefore
every character outside the allow-list ('&', '/', '?', '#', '%', space...) is a parameter
injection risk and is rejected here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from mlbooster.domain.errors import InvalidInput

_SERIES_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.]{0,63}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")

MIN_DATE = date(1950, 1, 1)
MAX_SERIES_PER_REQUEST = 50


def series_code(value: str) -> str:
    if not isinstance(value, str) or not _SERIES_CODE.fullmatch(value):
        raise InvalidInput(f"Geçersiz seri/veri grubu kodu: {value!r} (izin verilenler: harf, rakam, '.', '_'; en fazla 64 karakter)")
    return value


def series_codes(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        raise InvalidInput("En az bir seri kodu gerekli")
    if len(values) > MAX_SERIES_PER_REQUEST:
        raise InvalidInput(f"Tek istekte en fazla {MAX_SERIES_PER_REQUEST} seri istenebilir")
    return tuple(dict.fromkeys(series_code(v) for v in values))


# `what` is a code-side key; the message shows its Turkish label.
_WHAT_LABELS = {
    "identifier": "kimlik",
    "source": "kaynak",
    "sector": "sektör",
    "feature": "feature adı",
    "derived_feature": "türetilmiş feature adı",
    "event": "olay adı",
}


def slug(value: str, what: str = "identifier") -> str:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        label = _WHAT_LABELS.get(what, what)
        raise InvalidInput(f"Geçersiz {label}: {value!r} (izin verilenler: küçük harf, rakam, '_'; en fazla 32 karakter)")
    return value


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise InvalidInput(f"Başlangıç tarihi ({self.start}) bitiş tarihinden ({self.end}) sonra olamaz")
        if self.start < MIN_DATE:
            raise InvalidInput(f"Başlangıç tarihi {MIN_DATE} öncesine gidemez")
        if self.end > date.today() + timedelta(days=366):
            raise InvalidInput("Bitiş tarihi bugünden bir yıldan fazla ileride olamaz")
