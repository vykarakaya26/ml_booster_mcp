"""Search term catalog: catalog_data/search_terms.yaml, strictly validated with pydantic."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mlbooster.domain.errors import InconsistentCatalog
from mlbooster.domain.services.search_share import SectorSearchTerms, normalize
from mlbooster.domain.validation import slug
from mlbooster.infrastructure.catalog_yaml import _read_yaml

MAX_TERMS = 40
MAX_TERM_LENGTH = 60


class _SectorTermsSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generic: list[str] = Field(default_factory=list, max_length=MAX_TERMS)
    brands: list[str] = Field(default_factory=list, max_length=MAX_TERMS)

    @field_validator("generic", "brands")
    @classmethod
    def _terms(cls, values: list[str]) -> list[str]:
        for value in values:
            if not 2 <= len(value) <= MAX_TERM_LENGTH or not normalize(value):
                raise ValueError(f"Geçersiz arama terimi: {value!r}")
        return values


class _SearchTermsSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sectors: dict[str, _SectorTermsSchema]

    @field_validator("sectors")
    @classmethod
    def _ids(cls, value: dict[str, _SectorTermsSchema]) -> dict[str, _SectorTermsSchema]:
        for sector_id in value:
            slug(sector_id, "sector")
        return value


class YamlSearchTerms:
    def __init__(self, path: Path):
        self._path = path
        self._cache: dict[str, SectorSearchTerms] | None = None

    def load(self) -> dict[str, SectorSearchTerms]:
        if self._cache is None:
            if not self._path.exists():  # a user catalog directory may not carry this file
                return {}
            try:
                schema = _SearchTermsSchema.model_validate(_read_yaml(self._path))
            except ValidationError as exc:
                raise InconsistentCatalog(f"Arama terimi kataloğu schema hatası: {exc}") from None
            self._cache = {k: SectorSearchTerms(tuple(v.generic), tuple(v.brands)) for k, v in schema.sectors.items()}
        return self._cache
