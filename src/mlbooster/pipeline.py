"""Uçtan uca akış: adapter → bronze + silver → aylık gold panel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from mlbooster.adapters import REGISTRY, HealthResult, get_adapter
from mlbooster.adapters.base import AdapterError
from mlbooster.catalog import Catalog, MacroFeature, load_catalog
from mlbooster.config import data_dir
from mlbooster.store.bronze import BronzeStore
from mlbooster.store.silver import SilverStore
from mlbooster.transform.deflate import apply_transform, deflate, index_from_pct_changes
from mlbooster.transform.frequency import to_monthly

DEFAULT_START = date(2010, 1, 1)
CPI_SERIES = ("evds3", "TP.FG.J0")


class Workspace:
    def __init__(self, root: Path | None = None, catalog: Catalog | None = None):
        self.root = root or data_dir()
        self.catalog = catalog or load_catalog()
        self.bronze = BronzeStore(self.root / "bronze")
        self.silver = SilverStore(self.root / "silver")

    def adapter(self, source_id: str, **kwargs):
        return get_adapter(self.catalog.source(source_id), bronze=self.bronze, **kwargs)

    def fetch(
        self,
        source_id: str,
        series_ids: list[str],
        start: date,
        end: date | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        adapter = self.adapter(source_id)
        try:
            df = adapter.fetch(series_ids, start, end or date.today(), **kwargs)
        finally:
            adapter.close()
        self.silver.write(df)
        return df

    def health(self, source_id: str | None = None) -> list[HealthResult]:
        sources = [self.catalog.source(source_id)] if source_id else list(self.catalog.sources.values())
        results = []
        for source in sorted(sources, key=lambda s: s.priority or 99):
            if source.adapter not in REGISTRY:
                results.append(HealthResult(source.id, "skipped", "adapter henüz yazılmadı"))
                continue
            adapter = self.adapter(source.id)
            try:
                results.append(adapter.healthcheck())
            finally:
                adapter.close()
        return results

    # --- Gold panel ----------------------------------------------------------------------

    def _series_monthly(self, feature: MacroFeature, start: date, end: date, refresh: bool) -> pd.Series:
        source = self.catalog.source(feature.source)
        seed = source.seed(feature.series)
        df = pd.DataFrame() if refresh else self.silver.read(source.id, [feature.series])
        df = df[df["metric"] == "level"] if not df.empty else df
        if seed and not df.empty:
            df = df[df["freq"] == seed.freq]
        if df.empty:
            df = self.fetch(source.id, [feature.series], start, end)
            df = df[df["metric"] == "level"]
        if df.empty:
            raise AdapterError(f"{feature.series}: veri yok")
        freq = df["freq"].iloc[0]
        s = pd.Series(df["value"].to_numpy(), index=pd.to_datetime(df["date"]))
        lag = int(df["publication_lag_days"].iloc[0] or 0)
        return to_monthly(s, freq, feature.agg, seed.kind if seed else "level", lag)

    def _cpi_index(self, start: date, end: date, refresh: bool) -> pd.Series:
        source_id, code = CPI_SERIES
        df = pd.DataFrame() if refresh else self.silver.read(source_id, [code])
        df = df[df["metric"] == "pct_change"] if not df.empty else df
        if df.empty:
            df = self.fetch(source_id, [code], start, end, formula=1)
            df = df[df["metric"] == "pct_change"]
        pct = pd.Series(df["value"].to_numpy(), index=pd.to_datetime(df["date"]))
        lag = int(df["publication_lag_days"].iloc[0] or 0)
        return index_from_pct_changes(to_monthly(pct, "M", publication_lag_days=lag))

    def build_panel(
        self,
        sector_id: str,
        start: date = DEFAULT_START,
        end: date | None = None,
        refresh: bool = False,
    ) -> PanelResult:
        end = end or date.today()
        sector = self.catalog.sector(sector_id)
        result = PanelResult(sector_id)
        columns, cpi = {}, None
        for feature in sector.macro_features:
            if feature.series is None:
                result.skipped[feature.name] = f"seri kodu keşif bekliyor: {feature.discover_hint}"
                continue
            if self.catalog.source(feature.source).adapter not in REGISTRY:
                result.skipped[feature.name] = f"{feature.source} adapter'ı henüz yok"
                continue
            try:
                monthly = self._series_monthly(feature, start, end, refresh)
                if feature.real:
                    cpi = cpi if cpi is not None else self._cpi_index(start, end, refresh)
                    monthly = deflate(monthly, cpi)
            except AdapterError as exc:
                result.skipped[feature.name] = f"{type(exc).__name__}: {exc}"
                continue
            full = pd.date_range(monthly.index.min(), monthly.index.max(), freq="ME")
            columns[feature.name] = apply_transform(monthly.reindex(full), feature.transform)
        panel = pd.DataFrame(columns)
        if not panel.empty:
            panel = panel.loc[pd.Timestamp(start) : pd.Timestamp(end)]
            panel.index.name = "date"
        result.panel = panel
        return result


@dataclass
class PanelResult:
    sector_id: str
    panel: pd.DataFrame = field(default_factory=pd.DataFrame)
    skipped: dict[str, str] = field(default_factory=dict)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.panel.to_parquet(path)
        return path
