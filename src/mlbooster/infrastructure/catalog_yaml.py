"""YAML katalog deposu. Dosyalar pydantic şemalarıyla katı biçimde doğrulanıp domain nesnelerine çevrilir."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date
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
from mlbooster.infrastructure.settings import ensure_private_dir

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


class OverrideSeedSchema(SeedSchema):
    verified_at: str | None = None


class OverridesSchema(_Strict):
    """~/.mlbooster/catalog/overrides.yaml — kullanıcının keşfettiği seriler."""

    version: Literal[1] = 1
    seeds: dict[str, list[OverrideSeedSchema]] = Field(default_factory=dict)  # kaynak → tohum seriler
    features: dict[str, dict[str, str]] = Field(default_factory=dict)  # sektör → mevcut feature → seri kodu
    new_features: dict[str, list[FeatureSchema]] = Field(default_factory=dict)  # sektör → eklenen feature'lar

    @field_validator("features")
    @classmethod
    def _valid_codes(cls, v: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        for sector, mapping in v.items():
            slug(sector, "sektör")
            for code in mapping.values():
                series_code(code)
        return v


class YamlCatalogRepository:
    """Paket kataloğu + (varsa) kullanıcı override'ları. CatalogRepository ve CatalogOverrides port'larını uygular."""

    def __init__(self, root: Path, overrides_path: Path | None = None):
        self._root = root
        self._overrides_path = overrides_path
        self._cache: Catalog | None = None

    def load(self) -> Catalog:
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    def _load(self) -> Catalog:
        try:
            sources = [SourceSchema.model_validate(_read_yaml(p)).to_domain() for p in self._files("sources")]
            sectors = [SectorSchema.model_validate(_read_yaml(p)).to_domain() for p in self._files("sectors")]
            overrides = self._read_overrides()
        except ValidationError as exc:
            raise InconsistentCatalog(f"Katalog şema hatası: {exc}") from None
        source_map = {s.id: s for s in sources}
        sector_map = {s.id: s for s in sectors}
        for source_id, seeds in overrides.seeds.items():
            if source_id in source_map:
                extra = {s.code: SeedSeries(**s.model_dump(exclude={"verified_at"})) for s in seeds}
                kept = tuple(s for s in source_map[source_id].series if s.code not in extra)
                source_map[source_id] = replace(source_map[source_id], series=kept + tuple(extra.values()))
        for sector_id, added in overrides.new_features.items():
            if sector_id not in sector_map:
                raise InconsistentCatalog(f"Override: bilinmeyen sektör {sector_id}")
            sector = sector_map[sector_id]
            existing = {f.name for f in sector.macro_features}
            extra = tuple(f.to_domain() for f in added if f.name not in existing)
            sector_map[sector_id] = replace(sector, macro_features=sector.macro_features + extra)
        for sector_id, mapping in overrides.features.items():
            if sector_id not in sector_map:
                raise InconsistentCatalog(f"Override: bilinmeyen sektör {sector_id}")
            sector = sector_map[sector_id]
            unknown = set(mapping) - {f.name for f in sector.macro_features}
            if unknown:  # sessizce yok saymak yerine açıkça hata ver
                raise InconsistentCatalog(
                    f"Override: {sector_id} sektöründe olmayan feature(lar): {sorted(unknown)}. "
                    "Yeni feature için `catalog set-feature --create` kullanın."
                )
            features = tuple(
                replace(f, series=mapping[f.name]) if f.name in mapping else f for f in sector.macro_features
            )
            sector_map[sector_id] = replace(sector, macro_features=features)
        return Catalog(sources=source_map, sectors=sector_map)

    def _read_overrides(self) -> OverridesSchema:
        if self._overrides_path is None or not self._overrides_path.exists():
            return OverridesSchema()
        return OverridesSchema.model_validate(_read_yaml(self._overrides_path))

    def set_feature(
        self,
        sector_id: str,
        feature: str,
        seed: SeedSeries,
        source_id: str,
        new_feature: MacroFeature | None = None,
    ) -> str:
        if self._overrides_path is None:
            raise InconsistentCatalog("Override dosyası yapılandırılmamış")
        current = self._read_overrides().model_dump(mode="json")
        seeds = [s for s in current["seeds"].get(source_id, []) if s["code"] != seed.code]
        seeds.append(
            {
                "code": seed.code, "name": seed.name, "freq": seed.freq.value, "kind": seed.kind.value,
                "unit": seed.unit, "currency": seed.currency, "datagroup": seed.datagroup,
                "publication_lag_days": seed.publication_lag_days, "canary": False,
                "verified_at": date.today().isoformat(),
            }
        )  # fmt: skip
        current["seeds"][source_id] = seeds
        if new_feature is not None:
            added = [f for f in current["new_features"].get(sector_id, []) if f["name"] != feature]
            added.append(
                {
                    "name": new_feature.name, "source": new_feature.source, "series": seed.code,
                    "transform": new_feature.transform.value,
                    "agg": new_feature.agg.value if new_feature.agg else None,
                    "real": new_feature.real, "discover_hint": None,
                }
            )  # fmt: skip
            current["new_features"][sector_id] = added
        else:
            current["features"].setdefault(sector_id, {})[feature] = seed.code
        OverridesSchema.model_validate(current)  # yazmadan önce doğrula
        _atomic_write_yaml(self._overrides_path, current)
        self._cache = None  # sonraki load() yeni değeri görsün
        return str(self._overrides_path)

    def _files(self, kind: str) -> list[Path]:
        return sorted((self._root / kind).glob("*.yaml"))


def _atomic_write_yaml(path: Path, data: dict) -> None:
    ensure_private_dir(path.parent)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(
        "# mlbooster kullanıcı override'ları — `mlbooster catalog set-feature` ile yönetilir.\n"
        + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
