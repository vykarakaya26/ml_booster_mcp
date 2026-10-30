"""YAML katalog deposu. Dosyalar pydantic şemalarıyla katı biçimde doğrulanıp domain nesnelerine çevrilir."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mlbooster.domain.errors import InconsistentCatalog
from mlbooster.domain.model import (
    Access,
    Aggregation,
    AuthKind,
    AuthSpec,
    Catalog,
    Frequency,
    LicenseSpec,
    MacroFeature,
    RateLimitSpec,
    Sector,
    SeedSeries,
    SeriesKind,
    ShareSource,
    Source,
    Transform,
    Verification,
)
from mlbooster.domain.validation import series_code, slug

MAX_YAML_BYTES = 256 * 1024


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthSchema(_Strict):
    type: AuthKind
    header_name: str | None = None
    env_var: str | None = None
    env_vars: list[str] = Field(default_factory=list)
    ticket_url: str | None = None
    ttl_hours: float | None = None


class RateLimitSchema(_Strict):
    rps: float | None = Field(default=None, gt=0, le=100)
    per_minute: int | None = None
    backoff: Literal["exponential", "fixed"] = "exponential"
    max_obs_per_call: int | None = Field(default=None, ge=1, le=100_000)
    blocked_seconds_on_429: int | None = None
    notes: str | None = None


class LicenseSchema(_Strict):
    commercial: bool | None = None
    redistribution: bool | None = None
    attribution: Literal["required", "recommended", "unknown"] = "unknown"
    notes: str


class SeedSchema(_Strict):
    code: str
    name: str
    freq: Frequency
    kind: SeriesKind = SeriesKind.LEVEL
    unit: str | None = None
    currency: str | None = None
    datagroup: str | None = None
    publication_lag_days: int | None = Field(default=None, ge=0, le=730)
    canary: bool = False

    @field_validator("code", "datagroup")
    @classmethod
    def _codes(cls, v: str | None) -> str | None:
        return series_code(v) if v is not None else v


class SourceSchema(_Strict):
    id: str
    name: str
    owner: str
    base_url: str
    access: Access
    auth: AuthSchema
    rate_limit: RateLimitSchema = Field(default_factory=RateLimitSchema)
    license: LicenseSchema
    frequency: list[str]
    formats: list[str]
    sectors: list[str]
    publication_lag_days: int = Field(ge=0, le=730)
    verification_status: Verification
    verification_notes: str | None = None
    known_breaks: list[str] = Field(default_factory=list)
    adapter: str | None = None
    priority: int = 99
    series: list[SeedSchema] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return slug(v, "kaynak")

    @field_validator("base_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("base_url https olmalı")
        return v

    def to_domain(self) -> Source:
        a = self.auth
        return Source(
            id=self.id,
            name=self.name,
            owner=self.owner,
            base_url=self.base_url,
            access=self.access,
            auth=AuthSpec(
                a.type, a.header_name, tuple(a.env_vars or ([a.env_var] if a.env_var else [])), a.ticket_url, a.ttl_hours
            ),
            rate_limit=RateLimitSpec(
                self.rate_limit.rps,
                self.rate_limit.per_minute,
                self.rate_limit.max_obs_per_call,
                self.rate_limit.blocked_seconds_on_429,
                self.rate_limit.notes,
            ),
            license=LicenseSpec(
                self.license.commercial, self.license.redistribution, self.license.attribution, self.license.notes
            ),
            frequencies=tuple(self.frequency),
            formats=tuple(self.formats),
            sectors=tuple(self.sectors),
            publication_lag_days=self.publication_lag_days,
            verification=self.verification_status,
            verification_notes=self.verification_notes,
            known_breaks=tuple(self.known_breaks),
            provider=self.adapter,
            priority=self.priority,
            series=tuple(SeedSeries(**s.model_dump()) for s in self.series),
        )


class FeatureSchema(_Strict):
    name: str
    source: str
    series: str | None = None
    transform: Transform = Transform.LEVEL
    agg: Aggregation | None = None
    real: bool = False
    discover_hint: str | None = None

    @field_validator("series")
    @classmethod
    def _series(cls, v: str | None) -> str | None:
        return series_code(v) if v is not None else v

    def to_domain(self) -> MacroFeature:
        if self.series is None and not self.discover_hint:
            raise InconsistentCatalog(f"{self.name}: series boşsa discover_hint zorunlu")
        return MacroFeature(**self.model_dump())


class ShareSchema(_Strict):
    source: str
    metric: str
    is_proxy: bool = False
    notes: str | None = None


class SectorSchema(_Strict):
    id: str
    name: str
    macro_features: list[FeatureSchema] = Field(default_factory=list)
    sector_sources: list[str] = Field(default_factory=list)
    market_share_sources: list[ShareSchema] = Field(default_factory=list)
    ml_use_cases: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return slug(v, "sektör")

    def to_domain(self) -> Sector:
        return Sector(
            id=self.id,
            name=self.name,
            macro_features=tuple(f.to_domain() for f in self.macro_features),
            sector_sources=tuple(self.sector_sources),
            market_share_sources=tuple(ShareSource(**m.model_dump()) for m in self.market_share_sources),
            ml_use_cases=tuple(self.ml_use_cases),
        )


def _read_yaml(path: Path) -> dict:
    if path.stat().st_size > MAX_YAML_BYTES:
        raise InconsistentCatalog(f"Katalog dosyası çok büyük: {path.name}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))  # safe_load: nesne oluşturma yok
    except yaml.YAMLError:
        raise InconsistentCatalog(f"Geçersiz YAML: {path.name}") from None
    if not isinstance(data, dict):
        raise InconsistentCatalog(f"Katalog dosyası bir eşleme olmalı: {path.name}")
    return data


class YamlCatalogRepository:
    def __init__(self, root: Path):
        self._root = root
        self._cache: Catalog | None = None

    def load(self) -> Catalog:
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    def _load(self) -> Catalog:
        try:
            sources = [SourceSchema.model_validate(_read_yaml(p)).to_domain() for p in self._files("sources")]
            sectors = [SectorSchema.model_validate(_read_yaml(p)).to_domain() for p in self._files("sectors")]
        except ValidationError as exc:
            raise InconsistentCatalog(f"Katalog şema hatası: {exc}") from None
        return Catalog(sources={s.id: s for s in sources}, sectors={s.id: s for s in sectors})

    def _files(self, kind: str) -> list[Path]:
        return sorted((self._root / kind).glob("*.yaml"))
