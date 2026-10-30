"""Sektör gold paneli: makro feature'lar → aylık, nokta-zamanlı hizalanmış tablo."""

from __future__ import annotations

from datetime import date

import pandas as pd

from mlbooster.application.dto import FetchRequest, PanelResult
from mlbooster.application.errors import ProviderError
from mlbooster.application.ports import CatalogRepository, ObservationStore, PanelStore, ProviderFactory
from mlbooster.application.use_cases.series import FetchSeries
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Catalog, Frequency, MacroFeature, SeriesKind
from mlbooster.domain.services.deflation import deflate, index_from_pct_changes
from mlbooster.domain.services.features import apply_transform
from mlbooster.domain.services.frequency import to_monthly
from mlbooster.domain.validation import DateRange, slug

DEFAULT_START = date(2010, 1, 1)
CPI = ("evds3", "TP.TUKFIY2025.GENEL")  # deflatör: TÜFE aylık % değişimi (formül 1)


class BuildPanel:
    def __init__(
        self,
        catalog: CatalogRepository,
        providers: ProviderFactory,
        store: ObservationStore,
        panels: PanelStore,
    ):
        self._catalog = catalog
        self._providers = providers
        self._store = store
        self._panels = panels
        self._fetch = FetchSeries(catalog, providers, store)

    def __call__(
        self,
        sector_id: str,
        start: date = DEFAULT_START,
        end: date | None = None,
        refresh: bool = False,
        save: bool = True,
    ) -> PanelResult:
        period = DateRange(start, end or date.today())
        catalog = self._catalog.load()
        sector = catalog.sector(slug(sector_id, "sektör"))
        available = self._providers.available()
        result = PanelResult(sector.id)
        columns: dict[str, pd.Series] = {}
        cpi: pd.Series | None = None

        for feature in sector.macro_features:
            if not feature.is_resolved:
                result.skipped[feature.name] = f"seri kodu keşif bekliyor: {feature.discover_hint}"
                continue
            if catalog.source(feature.source).provider not in available:
                result.skipped[feature.name] = f"{feature.source} sağlayıcısı henüz yok"
                continue
            try:
                monthly = self._monthly(catalog, feature, period, refresh)
                if feature.real:
                    cpi = cpi if cpi is not None else self._cpi_index(catalog, period, refresh)
                    monthly = deflate(monthly, cpi)
            except (ProviderError, InvalidInput) as exc:
                result.skipped[feature.name] = f"{type(exc).__name__}: {exc}"
                continue
            columns[feature.name] = apply_transform(monthly, feature.transform)

        panel = pd.DataFrame(columns)
        if not panel.empty:
            panel = panel.loc[pd.Timestamp(period.start) : pd.Timestamp(period.end)]
            panel.index.name = "date"
        result.panel = panel
        if save and not panel.empty:
            result.location = self._panels.save(sector.id, panel)
        return result

    def sector(self, sector_id: str):
        """Sektör tanımı (analiz use case'leri feature meta verisi için kullanır)."""
        return self._catalog.load().sector(slug(sector_id, "sektör"))

    def _observations(
        self, catalog: Catalog, source_id: str, code: str, period: DateRange, metric: str, formula: int, refresh: bool
    ) -> pd.DataFrame:
        """Önce depodan okur; depo istenen başlangıcı kapsamıyorsa sağlayıcıdan yeniden çeker.
        Tek bir frekansa indirger (ör. önceden çekilmiş üç aylık kopyalar aylık veriyle karışmaz)."""
        seed = catalog.source(source_id).seed(code)
        df = pd.DataFrame() if refresh else self._store.read(source_id, [code])
        df = _single_series(df, metric, seed.freq.value if seed else None)
        if df.empty or not _covers_start(df, period.start):  # çekerken tamponlu, önbellekte istenen başlangıç yeterli
            request = FetchRequest.create(source_id, [code], _with_history(period.start), period.end, formula=formula)
            df = _single_series(self._fetch(request), metric, seed.freq.value if seed else None)
        if df.empty:
            raise ProviderError(f"{code}: veri yok")
        return df

    def _monthly(self, catalog: Catalog, feature: MacroFeature, period: DateRange, refresh: bool) -> pd.Series:
        df = self._observations(catalog, feature.source, feature.series, period, "level", 0, refresh)
        seed = catalog.source(feature.source).seed(feature.series)
        s = pd.Series(df["value"].astype(float).to_numpy(), index=pd.to_datetime(df["date"]))
        return to_monthly(
            s,
            Frequency(df["freq"].iloc[0]),
            feature.agg,
            seed.kind if seed else SeriesKind.LEVEL,
            int(df["publication_lag_days"].iloc[0] or 0),
        )

    def _cpi_index(self, catalog: Catalog, period: DateRange, refresh: bool) -> pd.Series:
        source_id, code = CPI
        df = self._observations(catalog, source_id, code, period, "pct_change", 1, refresh)
        pct = pd.Series(df["value"].astype(float).to_numpy(), index=pd.to_datetime(df["date"]))
        lag = int(df["publication_lag_days"].iloc[0] or 0)
        return index_from_pct_changes(to_monthly(pct, Frequency.MONTHLY, publication_lag_days=lag))


def _single_series(df: pd.DataFrame, metric: str, freq: str | None) -> pd.DataFrame:
    if df.empty:
        return df
    df = df[df["metric"] == metric]
    if df.empty:
        return df
    return df[df["freq"] == (freq or df["freq"].iloc[0])]


def _covers_start(df: pd.DataFrame, start: date) -> bool:
    """İlk gözlem, istenen başlangıçtan en fazla bir dönem (+%10) sonra mı?"""
    freq = Frequency(df["freq"].iloc[0])
    tolerance = max(7, int(freq.min_period_days * 1.1))
    return pd.Timestamp(df["date"].min()) <= pd.Timestamp(start) + pd.Timedelta(days=tolerance)


HISTORY_DAYS = 400  # yoy_pct (12 ay) + yayın gecikmesi için başlangıçtan önceki tampon


def _with_history(start: date) -> date:
    """Dönüşümler (yıllık değişim, fark) panelin ilk aylarında da dolu olsun diye tampon geçmiş."""
    from datetime import timedelta

    return max(date(1950, 1, 1), start - timedelta(days=HISTORY_DAYS))
