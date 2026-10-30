"""Seri keşfi: anahtar kelimeyle arama ve bulunan kodu sektör feature'ına bağlama."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, ProviderNotImplemented
from mlbooster.application.ports import CatalogOverrides, CatalogRepository, ProviderFactory
from mlbooster.application.use_cases._providers import open_provider
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, SeedSeries, SeriesKind
from mlbooster.domain.validation import series_code, slug

MAX_QUERY_LEN = 80


class SearchSeries:
    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory):
        self._catalog = catalog
        self._providers = providers

    def __call__(self, text: str, source_id: str = "evds3", limit: int = 20) -> pd.DataFrame:
        text = (text or "").strip()
        if not 2 <= len(text) <= MAX_QUERY_LEN:
            raise InvalidInput(f"Arama metni 2–{MAX_QUERY_LEN} karakter olmalı")
        source = self._catalog.load().source(slug(source_id, "kaynak"))
        with open_provider(self._providers, source) as provider:
            search = getattr(provider, "search", None)
            if search is None:
                raise ProviderNotImplemented(f"{source.id} arama desteklemiyor")
            return search(text, max(1, min(limit, 100)))


@dataclass(frozen=True)
class FeatureBinding:
    sector_id: str
    feature: str
    seed: SeedSeries
    last_observation: date
    location: str


class SetFeatureSeries:
    """Keşif bekleyen (veya yanlış) bir feature'a seri kodu bağlar.

    Bağlamadan önce kod canlı doğrulanır: son 2 yılda veri dönmeli ve seri durmuş olmamalı.
    Yayın gecikmesi verilmezse frekansa göre muhafazakâr varsayılan kullanılır (look-ahead'e karşı).
    """

    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory, overrides: CatalogOverrides):
        self._catalog = catalog
        self._providers = providers
        self._overrides = overrides

    def __call__(
        self,
        sector_id: str,
        feature: str,
        code: str,
        name: str | None = None,
        kind: str = "level",
        publication_lag_days: int | None = None,
        datagroup: str | None = None,
    ) -> FeatureBinding:
        catalog = self._catalog.load()
        sector = catalog.sector(slug(sector_id, "sektör"))
        match = next((f for f in sector.macro_features if f.name == feature), None)
        if match is None:
            raise InvalidInput(f"{sector.id} sektöründe '{feature}' feature'ı yok: {[f.name for f in sector.macro_features]}")
        code = series_code(code)
        try:
            kind_v = SeriesKind(kind)
        except ValueError:
            raise InvalidInput(f"kind 'level' veya 'flow' olmalı: {kind}") from None
        if publication_lag_days is not None and not 0 <= publication_lag_days <= 730:
            raise InvalidInput("publication_lag_days 0–730 olmalı")

        source = catalog.source(match.source)
        today = date.today()
        request = FetchRequest.create(source.id, [code], today - timedelta(days=730), today)
        with open_provider(self._providers, source) as provider:
            df = provider.fetch(request)
        if df.empty:
            raise ProviderError(f"{code}: son 2 yılda gözlem yok; kod yanlış veya seri arşivlenmiş olabilir")
        freq = Frequency(df["freq"].iloc[0])
        last = max(df["date"])
        if (today - last).days > freq.max_staleness_days:
            raise ProviderError(f"{code}: son gözlem {last}; seri durmuş görünüyor, güncel bir kod seçin")

        seed = SeedSeries(
            code=code,
            name=name or feature,
            freq=freq,
            kind=kind_v,
            datagroup=series_code(datagroup) if datagroup else None,
            publication_lag_days=(
                publication_lag_days if publication_lag_days is not None else freq.default_publication_lag_days
            ),
        )
        location = self._overrides.set_feature(sector.id, feature, seed, source.id)
        return FeatureBinding(sector.id, feature, seed, last, location)
