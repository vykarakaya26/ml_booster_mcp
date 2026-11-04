"""Value objects for identifiers and date ranges coming from the outside world.

Series codes are appended to provider URLs (to the path, for EVDS) without encoding. Therefore
every character outside the allow-list ('&', '/', '?', '#', '%', space...) is a parameter
injection risk and is rejected here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from mlbooster.domain.errors import InvalidInput

_SERIES_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.]{0,63}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")
_BRAND_CHOICE = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}:[A-Za-z0-9_.]{1,64}(\+[A-Za-z0-9_.]{1,64}){0,7}$")

_EVIDENCE_ID = re.compile(r"^[0-9a-f]{16}$")

MIN_DATE = date(1950, 1, 1)
MAX_SERIES_PER_REQUEST = 50
BRAND_MAX = 80


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


def brand_query(value: str) -> str:
    """Brand or company name typed by the user: only compared with catalog and entity names, never sent to a source."""
    if not isinstance(value, str):
        raise InvalidInput("Marka adı metin olmalıdır")
    text = " ".join(unicodedata.normalize("NFC", value).split())
    if any(unicodedata.category(c) == "Cc" for c in text):
        raise InvalidInput("Marka adında kontrol karakteri olamaz")
    if len(text) > BRAND_MAX:
        raise InvalidInput(f"Marka adı en fazla {BRAND_MAX} karakter olabilir")
    if sum(c.isalnum() for c in text) < 2:
        raise InvalidInput("Marka adı en az iki harf ya da rakam içermelidir")
    return text


def brand_choice(value: str) -> str:
    """Candidate id returned by the brand search: `<source>:<entity id>[+<entity id>...]`."""
    if not isinstance(value, str) or not _BRAND_CHOICE.fullmatch(value):
        raise InvalidInput(f"Geçersiz aday kimliği: {value!r} (beklenen biçim: kaynak:oyuncu_kimliği)")
    return value


def evidence_id(value: str) -> str:
    if not isinstance(value, str) or not _EVIDENCE_ID.fullmatch(value):
        raise InvalidInput(f"Geçersiz bulgu kimliği: {value!r} (beklenen biçim: 16 karakterlik onaltılık kimlik)")
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
