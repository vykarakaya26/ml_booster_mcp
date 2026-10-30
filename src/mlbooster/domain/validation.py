"""Dış dünyadan gelen tanımlayıcılar ve tarih aralıkları için değer nesneleri.

Seri kodları sağlayıcı URL'lerine (EVDS'de path'e) kodlanmadan eklenir. Bu yüzden izin
listesi dışındaki her karakter ('&', '/', '?', '#', '%', boşluk...) parametre enjeksiyonu
riskidir ve burada reddedilir.
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
        raise InvalidInput(f"Geçersiz seri/veri grubu kodu: {value!r} (izinli: harf, rakam, '.', '_'; ≤64)")
    return value


def series_codes(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        raise InvalidInput("En az bir seri kodu gerekli")
    if len(values) > MAX_SERIES_PER_REQUEST:
        raise InvalidInput(f"Tek istekte en fazla {MAX_SERIES_PER_REQUEST} seri")
    return tuple(dict.fromkeys(series_code(v) for v in values))


def slug(value: str, what: str = "kimlik") -> str:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise InvalidInput(f"Geçersiz {what}: {value!r} (izinli: küçük harf, rakam, '_'; ≤32)")
    return value


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise InvalidInput(f"Başlangıç ({self.start}) bitişten ({self.end}) sonra olamaz")
        if self.start < MIN_DATE:
            raise InvalidInput(f"Başlangıç {MIN_DATE} öncesi olamaz")
        if self.end > date.today() + timedelta(days=366):
            raise InvalidInput("Bitiş tarihi bir yıldan fazla ileride olamaz")
