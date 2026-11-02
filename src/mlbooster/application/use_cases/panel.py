"""Sector gold panel: macro features → monthly, point-in-time aligned table."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from mlbooster.application.dto import FetchRequest, PanelResult
from mlbooster.application.errors import ProviderError
from mlbooster.application.ports import CatalogRepository, ObservationStore, PanelStore, ProviderFactory
from mlbooster.application.use_cases.series import FetchSeries
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Catalog, Frequency, MacroFeature, SeriesKind
from mlbooster.domain.services.deflation import deflate, index_from_pct_changes
from mlbooster.domain.services.features import apply_transform
from mlbooster.domain.services.frequency import to_frequency, to_monthly
from mlbooster.domain.validation import DateRange, slug

DEFAULT_START = date(2010, 1, 1)
CPI = ("evds3", "TP.TUKFIY2025.GENEL")  # deflator: monthly % change of CPI (TÜFE) (formula 1)


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
        freq: str = "M",
    ) -> PanelResult:
        if freq not in {"M", "W", "B"}:
            raise InvalidInput(f"Panel frekansı M (aylık), W (haftalık) ya da B (iş günü) olmalıdır: {freq}")
        period = DateRange(start, end or date.today())
        catalog = self._catalog.load()
        sector = catalog.sector(slug(sector_id, "sector"))
        available = self._providers.available()
        result = PanelResult(sector.id)
        columns: dict[str, pd.Series] = {}
        cpi: pd.Series | None = None

        for feature in sector.macro_features:
            if not feature.is_resolved:
                result.skipped[feature.name] = f"seri kodu bekleniyor: {feature.discover_hint}"
                continue
            if catalog.source(feature.source).provider not in available:
                result.skipped[feature.name] = f"{feature.source} için henüz provider yok"
                continue
            try:
                monthly = self._monthly(catalog, feature, period, refresh, freq)
                if feature.real:
                    cpi = cpi if cpi is not None else self._cpi_index(catalog, period, refresh, freq)
                    monthly = deflate(monthly, cpi)
            except (ProviderError, InvalidInput) as exc:
                result.skipped[feature.name] = f"{type(exc).__name__}: {exc}"
                continue
            columns[feature.name] = apply_transform(monthly, feature.transform, freq)

        panel = pd.DataFrame(columns)
        if not panel.empty:
            panel = panel.loc[pd.Timestamp(period.start) : pd.Timestamp(period.end)]
            panel.index.name = "date"
        result.panel = panel
        if save and not panel.empty:
            result.location = self._panels.save(sector.id if freq == "M" else f"{sector.id}_{freq.lower()}", panel)
        return result

    def sector(self, sector_id: str):
        """Sector definition (used by the analysis use cases for feature metadata)."""
        return self._catalog.load().sector(slug(sector_id, "sector"))

    def _observations(
        self, catalog: Catalog, source_id: str, code: str, period: DateRange, metric: str, formula: int, refresh: bool
    ) -> pd.DataFrame:
        """Reads from the store first; refetches from the provider if the store does not cover the requested start.
        Reduces to a single frequency (e.g. previously fetched quarterly copies do not mix with monthly data)."""
        seed = catalog.source(source_id).seed(code)
        df = pd.DataFrame() if refresh else self._store.read(source_id, [code])
        df = _single_series(df, metric, seed.freq.value if seed else None)
        covered = not df.empty and (
            _covers_start(df, period.start) or _asked_from(self._store.requested_start(source_id, code, metric), period.start)
        )
        if not covered or _is_stale(df):  # fetch with a buffer; for the cache the requested start is sufficient
            request = FetchRequest.create(source_id, [code], _with_history(period.start), period.end, formula=formula)
            df = _single_series(self._fetch(request), metric, seed.freq.value if seed else None)
        if df.empty:
            raise ProviderError(f"{code}: veri yok")
        return df

    def _monthly(self, catalog: Catalog, feature: MacroFeature, period: DateRange, refresh: bool, freq: str = "M") -> pd.Series:
        df = self._observations(catalog, feature.source, feature.series, period, "level", 0, refresh)
        seed = catalog.source(feature.source).seed(feature.series)
        s = pd.Series(df["value"].astype(float).to_numpy(), index=pd.to_datetime(df["date"]))
        return to_frequency(
            s,
            Frequency(df["freq"].iloc[0]),
            freq,
            feature.agg,
            seed.kind if seed else SeriesKind.LEVEL,
            int(df["publication_lag_days"].iloc[0] or 0),
        )

    def _cpi_index(self, catalog: Catalog, period: DateRange, refresh: bool, freq: str = "M") -> pd.Series:
        source_id, code = CPI
        df = self._observations(catalog, source_id, code, period, "pct_change", 1, refresh)
        pct = pd.Series(df["value"].astype(float).to_numpy(), index=pd.to_datetime(df["date"]))
        lag = int(df["publication_lag_days"].iloc[0] or 0)
        index = index_from_pct_changes(to_monthly(pct, Frequency.MONTHLY, publication_lag_days=lag))
        return index if freq == "M" else to_frequency(index, Frequency.MONTHLY, freq)  # already lagged


def _single_series(df: pd.DataFrame, metric: str, freq: str | None) -> pd.DataFrame:
    """One metric at one frequency, aggregate rows only (series with entity rows, e.g. BDDK bank groups, keep their
    total under an empty entity_id)."""
    if df.empty:
        return df
    df = df[(df["metric"] == metric) & (df["entity_id"].fillna("") == "")]
    if df.empty:
        return df
    return df[df["freq"] == (freq or df["freq"].iloc[0])]


def _covers_start(df: pd.DataFrame, start: date) -> bool:
    """Is the first observation at most one period (+10%) after the requested start?"""
    freq = Frequency(df["freq"].iloc[0])
    tolerance = max(7, int(freq.min_period_days * 1.1))
    return pd.Timestamp(df["date"].min()) <= pd.Timestamp(start) + pd.Timedelta(days=tolerance)


def _asked_from(requested: date | None, start: date) -> bool:
    """If the provider was already asked from `start` or earlier, the series simply begins later than `start`
    even though the first cached observation is after it; fetching again would bring no new data."""
    return requested is not None and requested <= start


HISTORY_DAYS = 400  # buffer before the start for yoy_pct (12 months) + publication lag


def _with_history(start: date) -> date:
    """Buffer history so that transforms (year-on-year change, difference) are populated in the first panel months too."""
    from datetime import timedelta

    return max(date(1950, 1, 1), start - timedelta(days=HISTORY_DAYS))


def _is_stale(df: pd.DataFrame, now: datetime | None = None) -> bool:
    """Has the most recent cached pull exceeded the freshness window for the series frequency? (self-refresh)"""
    now = now or datetime.now(UTC)
    last_pull = pd.Timestamp(df["vintage_ts"].max())
    if last_pull.tzinfo is None:
        last_pull = last_pull.tz_localize("UTC")
    ttl = Frequency(df["freq"].iloc[0]).cache_ttl_hours
    return (pd.Timestamp(now) - last_pull) > pd.Timedelta(hours=ttl)
