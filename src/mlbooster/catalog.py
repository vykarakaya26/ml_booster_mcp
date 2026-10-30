"""Kaynak ve sektör kataloğu: catalog/sources/*.yaml ve catalog/sectors/*.yaml."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mlbooster.config import catalog_dir

Freq = Literal["D", "B", "W", "SM", "M", "Q", "S", "A", "H"]


class Access(StrEnum):
    rest_api = "rest_api"
    file_download = "file_download"
    pdf_report = "pdf_report"
    scrape = "scrape"


class Verification(StrEnum):
    # Uç bizim tarafımızdan canlı denendi.
    verified = "verified"
    # Yalnızca araştırma raporunda geçiyor; konnektör yazılırken doğrulanmalı.
    reported = "reported"
    unverified = "unverified"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Auth(_Strict):
    type: Literal["none", "header", "tgt", "registration"]
    header_name: str | None = None
    env_var: str | None = None
    env_vars: list[str] = Field(default_factory=list)
    ticket_url: str | None = None
    ttl_hours: float | None = None


class RateLimit(_Strict):
    rps: float | None = None
    per_minute: int | None = None
    backoff: Literal["exponential", "fixed"] = "exponential"
    max_obs_per_call: int | None = None
    blocked_seconds_on_429: int | None = None
    notes: str | None = None


class License(_Strict):
    commercial: bool | None = None
    redistribution: bool | None = None
    attribution: Literal["required", "recommended", "unknown"] = "unknown"
    notes: str | None = None


class SeedSeries(_Strict):
    code: str
    name: str
    freq: Freq
    # level: stok/fiyat/oran (ortalama veya son değer ile toplanır); flow: akım (toplanır)
    kind: Literal["level", "flow"] = "level"
    unit: str | None = None
    currency: str | None = None
    datagroup: str | None = None
    publication_lag_days: int | None = None
    canary: bool = False


class Source(_Strict):
    id: str
    name: str
    owner: str
    base_url: str
    access: Access
    auth: Auth
    rate_limit: RateLimit = Field(default_factory=RateLimit)
    license: License
    frequency: list[str]
    formats: list[str]
    sectors: list[str]
    publication_lag_days: int
    verification_status: Verification
    verification_notes: str | None = None
    known_breaks: list[str] = Field(default_factory=list)
    adapter: str | None = None  # adapters.REGISTRY anahtarı; None = henüz yazılmadı
    priority: int | None = None
    series: list[SeedSeries] = Field(default_factory=list)

    def seed(self, code: str) -> SeedSeries | None:
        return next((s for s in self.series if s.code == code), None)


class MacroFeature(_Strict):
    name: str
    source: str
    # None = seri kodu henüz katalogdan keşfedilmedi (/serieList veya EVDS-MCP ile bulunacak)
    series: str | None = None
    transform: Literal["level", "log_diff", "yoy_pct", "diff"] = "level"
    agg: Literal["avg", "last", "sum"] | None = None
    real: bool = False
    discover_hint: str | None = None

    @model_validator(mode="after")
    def _hint_when_pending(self) -> MacroFeature:
        if self.series is None and not self.discover_hint:
            raise ValueError(f"{self.name}: series boşsa discover_hint zorunlu")
        return self


class ShareSource(_Strict):
    source: str
    metric: str
    is_proxy: bool = False
    notes: str | None = None


class Sector(_Strict):
    id: str
    name: str
    macro_features: list[MacroFeature] = Field(default_factory=list)
    sector_sources: list[str] = Field(default_factory=list)
    market_share_sources: list[ShareSource] = Field(default_factory=list)
    ml_use_cases: list[str] = Field(default_factory=list)


class Catalog(BaseModel):
    sources: dict[str, Source]
    sectors: dict[str, Sector]

    def source(self, source_id: str) -> Source:
        try:
            return self.sources[source_id]
        except KeyError:
            raise KeyError(f"Bilinmeyen kaynak: {source_id}. Mevcut: {sorted(self.sources)}") from None

    def sector(self, sector_id: str) -> Sector:
        try:
            return self.sectors[sector_id]
        except KeyError:
            raise KeyError(f"Bilinmeyen sektör: {sector_id}. Mevcut: {sorted(self.sectors)}") from None

    def sources_for_sector(self, sector_id: str) -> list[Source]:
        sector = self.sector(sector_id)
        ids = {f.source for f in sector.macro_features}
        ids |= set(sector.sector_sources)
        ids |= {m.source for m in sector.market_share_sources}
        return sorted((self.sources[i] for i in ids), key=lambda s: (s.priority or 99, s.id))

    def check_references(self) -> list[str]:
        errors = []
        for sector in self.sectors.values():
            refs = [f.source for f in sector.macro_features] + sector.sector_sources
            refs += [m.source for m in sector.market_share_sources]
            errors += [f"{sector.id}: bilinmeyen kaynak {r}" for r in refs if r not in self.sources]
        return errors


def _load_dir(path: Path) -> list[dict]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(path.glob("*.yaml"))]


@lru_cache(maxsize=4)
def load_catalog(root: Path | None = None) -> Catalog:
    root = root or catalog_dir()
    sources = {s.id: s for s in (Source.model_validate(d) for d in _load_dir(root / "sources"))}
    sectors = {s.id: s for s in (Sector.model_validate(d) for d in _load_dir(root / "sectors"))}
    catalog = Catalog(sources=sources, sectors=sectors)
    if errors := catalog.check_references():
        raise ValueError("Katalog referans hataları:\n" + "\n".join(errors))
    return catalog
