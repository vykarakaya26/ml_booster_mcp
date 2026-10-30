"""Varlıklar ve değer nesneleri (değişmez dataclass'lar)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mlbooster.domain.errors import InconsistentCatalog, NotFound


class Frequency(StrEnum):
    HOURLY = "H"
    DAILY = "D"
    BUSINESS = "B"
    WEEKLY = "W"
    SEMI_MONTHLY = "SM"
    MONTHLY = "M"
    QUARTERLY = "Q"
    SEMI_ANNUAL = "S"
    ANNUAL = "A"

    @property
    def is_high(self) -> bool:
        """Aylıktan sık: aylığa indirgenirken agregasyon gerekir."""
        return self in {Frequency.HOURLY, Frequency.DAILY, Frequency.BUSINESS, Frequency.WEEKLY, Frequency.SEMI_MONTHLY}

    @property
    def is_low(self) -> bool:
        """Aylıktan seyrek: aylığa çıkarılırken ileriye doldurulur."""
        return self in {Frequency.QUARTERLY, Frequency.SEMI_ANNUAL, Frequency.ANNUAL}

    @property
    def min_period_days(self) -> int:
        """Bir gözlemin kapsadığı en kısa takvim süresi (muhafazakâr parçalama için)."""
        return _MIN_PERIOD_DAYS[self]

    @property
    def max_staleness_days(self) -> int:
        """Son gözlem bundan eskiyse seri durmuş/taşınmış sayılır (dönem + tipik yayın gecikmesi)."""
        return _MAX_STALENESS_DAYS[self]

    @property
    def default_publication_lag_days(self) -> int:
        """Yayın gecikmesi bilinmeyen yeni seriler için muhafazakâr varsayılan (look-ahead'e karşı)."""
        return _DEFAULT_LAG_DAYS[self]

    @property
    def rank(self) -> int:
        return list(Frequency).index(self)


_MIN_PERIOD_DAYS = {
    Frequency.HOURLY: 1, Frequency.DAILY: 1, Frequency.BUSINESS: 1, Frequency.WEEKLY: 7,
    Frequency.SEMI_MONTHLY: 14, Frequency.MONTHLY: 28, Frequency.QUARTERLY: 89,
    Frequency.SEMI_ANNUAL: 181, Frequency.ANNUAL: 365,
}  # fmt: skip
_MAX_STALENESS_DAYS = {
    Frequency.HOURLY: 3, Frequency.DAILY: 10, Frequency.BUSINESS: 10, Frequency.WEEKLY: 21,
    Frequency.SEMI_MONTHLY: 35, Frequency.MONTHLY: 75, Frequency.QUARTERLY: 200,
    Frequency.SEMI_ANNUAL: 380, Frequency.ANNUAL: 550,
}  # fmt: skip

_DEFAULT_LAG_DAYS = {
    Frequency.HOURLY: 1, Frequency.DAILY: 1, Frequency.BUSINESS: 1, Frequency.WEEKLY: 7,
    Frequency.SEMI_MONTHLY: 15, Frequency.MONTHLY: 30, Frequency.QUARTERLY: 90,
    Frequency.SEMI_ANNUAL: 120, Frequency.ANNUAL: 180,
}  # fmt: skip


class SeriesKind(StrEnum):
    LEVEL = "level"  # stok / fiyat / oran
    FLOW = "flow"  # akım: aylığa toplanarak indirgenir


class Aggregation(StrEnum):
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    FIRST = "first"
    LAST = "last"
    SUM = "sum"


class Transform(StrEnum):
    LEVEL = "level"
    DIFF = "diff"
    LOG_DIFF = "log_diff"
    YOY_PCT = "yoy_pct"


class Access(StrEnum):
    REST_API = "rest_api"
    FILE_DOWNLOAD = "file_download"
    PDF_REPORT = "pdf_report"
    SCRAPE = "scrape"


class AuthKind(StrEnum):
    NONE = "none"
    HEADER = "header"
    TGT = "tgt"
    REGISTRATION = "registration"


class Verification(StrEnum):
    VERIFIED = "verified"  # uç canlı denendi
    REPORTED = "reported"  # yalnızca araştırmada geçiyor
    UNVERIFIED = "unverified"


class HealthStatus(StrEnum):
    OK = "ok"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class AuthSpec:
    kind: AuthKind
    header_name: str | None = None
    env_vars: tuple[str, ...] = ()
    ticket_url: str | None = None
    ttl_hours: float | None = None


@dataclass(frozen=True)
class RateLimitSpec:
    rps: float | None = None
    per_minute: int | None = None
    max_obs_per_call: int | None = None
    blocked_seconds_on_429: int | None = None
    notes: str | None = None


@dataclass(frozen=True)
class LicenseSpec:
    commercial: bool | None
    redistribution: bool | None
    attribution: str
    notes: str


@dataclass(frozen=True)
class SeedSeries:
    code: str
    name: str
    freq: Frequency
    kind: SeriesKind = SeriesKind.LEVEL
    unit: str | None = None
    currency: str | None = None
    datagroup: str | None = None
    publication_lag_days: int | None = None
    canary: bool = False


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    owner: str
    base_url: str
    access: Access
    auth: AuthSpec
    rate_limit: RateLimitSpec
    license: LicenseSpec
    frequencies: tuple[str, ...]
    formats: tuple[str, ...]
    sectors: tuple[str, ...]
    publication_lag_days: int
    verification: Verification
    verification_notes: str | None = None
    known_breaks: tuple[str, ...] = ()
    provider: str | None = None  # None = bu kaynak için sağlayıcı (adapter) henüz yok
    priority: int = 99
    series: tuple[SeedSeries, ...] = ()

    def seed(self, code: str) -> SeedSeries | None:
        return next((s for s in self.series if s.code == code), None)

    def lag_for(self, code: str) -> int:
        seed = self.seed(code)
        return seed.publication_lag_days if seed and seed.publication_lag_days is not None else self.publication_lag_days

    @property
    def canaries(self) -> tuple[SeedSeries, ...]:
        return tuple(s for s in self.series if s.canary)


@dataclass(frozen=True)
class MacroFeature:
    name: str
    source: str
    series: str | None = None  # None = seri kodu henüz keşfedilmedi
    transform: Transform = Transform.LEVEL
    agg: Aggregation | None = None
    real: bool = False
    discover_hint: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.series is not None


@dataclass(frozen=True)
class ShareSource:
    source: str
    metric: str
    is_proxy: bool = False
    notes: str | None = None


@dataclass(frozen=True)
class Sector:
    id: str
    name: str
    macro_features: tuple[MacroFeature, ...] = ()
    sector_sources: tuple[str, ...] = ()
    market_share_sources: tuple[ShareSource, ...] = ()
    ml_use_cases: tuple[str, ...] = ()

    def referenced_sources(self) -> list[str]:
        ids = [f.source for f in self.macro_features] + list(self.sector_sources)
        ids += [m.source for m in self.market_share_sources]
        return list(dict.fromkeys(ids))


@dataclass(frozen=True)
class Catalog:
    sources: dict[str, Source]
    sectors: dict[str, Sector]

    def __post_init__(self) -> None:
        errors = [
            f"{sector.id}: bilinmeyen kaynak {ref}"
            for sector in self.sectors.values()
            for ref in sector.referenced_sources()
            if ref not in self.sources
        ]
        if errors:
            raise InconsistentCatalog("Katalog referans hataları: " + "; ".join(errors))

    def source(self, source_id: str) -> Source:
        if source_id not in self.sources:
            raise NotFound(f"Bilinmeyen kaynak: {source_id}. Mevcut: {', '.join(sorted(self.sources))}")
        return self.sources[source_id]

    def sector(self, sector_id: str) -> Sector:
        if sector_id not in self.sectors:
            raise NotFound(f"Bilinmeyen sektör: {sector_id}. Mevcut: {', '.join(sorted(self.sectors))}")
        return self.sectors[sector_id]

    def sources_by_priority(self) -> list[Source]:
        return sorted(self.sources.values(), key=lambda s: (s.priority, s.id))

    def sources_for_sector(self, sector_id: str) -> list[Source]:
        refs = self.sector(sector_id).referenced_sources()
        return sorted((self.sources[i] for i in refs), key=lambda s: (s.priority, s.id))


@dataclass(frozen=True)
class HealthResult:
    source_id: str
    status: HealthStatus
    detail: str
    latency_ms: float | None = None
    checked: tuple[str, ...] = field(default_factory=tuple)
