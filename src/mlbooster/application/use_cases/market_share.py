"""Market share analysis: user table or provider series with entity rows + (optional) sector macro panel."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError
from mlbooster.application.ports import CatalogRepository, ObservationStore, TableReader
from mlbooster.application.use_cases.panel import BuildPanel, _asked_from, _covers_start, _is_stale
from mlbooster.application.use_cases.series import FetchSeries
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, SeriesKind
from mlbooster.domain.services.frequency import to_monthly
from mlbooster.domain.services.market_share import MAX_ROWS, TOTAL_ALIASES, ShareConfig, analyze
from mlbooster.domain.validation import DateRange, series_code, slug

REVISION_DAYS = 400  # a stale cache is refreshed from about a year back (covers the sources' revision windows)
NEGATIVE_TOLERANCE = 0.005  # negative entity amounts up to 0.5% of the market total are counted as zero


class SourceShareTable:
    """Share table (entity, date, value) from a catalog series whose provider returns entity rows (e.g. BDDK bank
    groups). The series total (empty entity_id) becomes the market total row `TOPLAM`.

    Reads the local store first: a cache that covers the period is used as is; a stale one is topped up from about a
    year back instead of refetching the whole history (monthly bulletins answer one month per request).

    Sub-monthly series (e.g. EPİAŞ daily generation by source) are aggregated to calendar months first (flow → sum,
    level → mean, an incomplete last month is dropped): the share analysis works on monthly or coarser periods."""

    def __init__(self, catalog: CatalogRepository, fetch: FetchSeries, store: ObservationStore):
        self._catalog = catalog
        self._fetch = fetch
        self._store = store

    def __call__(
        self, source_id: str, series_id: str, start: date, end: date | None = None, refresh: bool = False
    ) -> tuple[pd.DataFrame, dict]:
        source = self._catalog.load().source(slug(source_id, "source"))
        code = series_code(series_id)
        seed = source.seed(code)
        if seed is None:
            known = ", ".join(s.code for s in source.series) or "yok"
            raise InvalidInput(f"{source.id} kataloğunda {code} serisi yok. Katalogdaki seriler: {known}")
        period = DateRange(start, end or date.today())
        fetch_error = None
        if (since := self._due(source.id, code, period, refresh)) is not None:
            try:
                self._fetch(FetchRequest.create(source.id, [code], since, period.end), refresh=refresh)
            except ProviderError as exc:
                if self._in_period(source.id, code, period).empty:
                    raise
                fetch_error = str(exc)
        df = self._in_period(source.id, code, period)
        if df.empty:
            raise InvalidInput(f"{code}: {period.start} – {period.end} aralığında gözlem yok")
        freq = Frequency(seed.freq)
        if freq.is_high:
            df = _monthly(df, freq, seed.kind)
            if df.empty:
                raise InvalidInput(f"{code}: {period.start} – {period.end} aralığında tamamlanmış ay yok")
        entity = df["entity_id"].fillna("") != ""
        if not entity.any():
            raise InvalidInput(f"{code} serisi oyuncu ya da grup kırılımı içermiyor; pazar payı için kırılımlı bir seri seçin")
        latest = _latest_names(df)
        names = df["entity_id"].map(latest).where(entity).fillna(df["entity_id"])
        if names[entity].str.casefold().isin([a.casefold() for a in TOTAL_ALIASES]).any():
            raise InvalidInput(f"{code}: oyuncu adı pazar toplamı işaretiyle çakışıyor")
        values, negative = _without_negatives(df, entity, code)
        table = pd.DataFrame(
            {"entity": names.where(entity, TOTAL_ALIASES[0]), "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
             "value": values}
        )  # fmt: skip
        meta = {
            "source": source.id, "series": code, "name": seed.name, "unit": seed.unit, "kind": seed.kind.value,
            "is_proxy": bool(df["is_proxy"].fillna(False).astype(bool).any()),
            "attribution": f"Kaynak: {source.owner} — {source.name}",
        }  # fmt: skip
        if negative:
            meta["negative_rows"] = negative
        if fetch_error:
            meta["fetch_error"] = fetch_error
        if freq.is_high:
            meta["aggregated_from"] = freq.value
        return table.reset_index(drop=True), meta

    def prefetch(self, source_id: str, series_ids: list[str], start: date, end: date | None = None, refresh: bool = False) -> None:
        """Fetches the series that are not cached in one provider pass: the series of a source come from the same
        monthly files, so asking for them one by one would download every file again."""
        source = self._catalog.load().source(slug(source_id, "source"))
        period = DateRange(start, end or date.today())
        due = {code: since for code in series_ids if (since := self._due(source.id, series_code(code), period, refresh)) is not None}
        if due:
            self._fetch(FetchRequest.create(source.id, list(due), min(due.values()), period.end), refresh=refresh)

    def labels(self, source_id: str, series_id: str, start: date, end: date | None = None) -> dict[str, str]:
        """entity_id → the label the share table of this period uses for it (cached rows only)."""
        period = DateRange(start, end or date.today())
        df = self._in_period(slug(source_id, "source"), series_code(series_id), period)
        return _latest_names(df).to_dict() if not df.empty else {}

    def _due(self, source_id: str, code: str, period: DateRange, refresh: bool) -> date | None:
        """Start to fetch the series from; None when the cache is enough."""
        cached = pd.DataFrame() if refresh else self._levels(source_id, code)
        if cached.empty or not (
            _covers_start(cached, period.start) or _asked_from(self._store.requested_start(source_id, code, "level"), period.start)
        ):
            return period.start
        if _is_stale(cached):
            return max(period.start, pd.Timestamp(cached["date"].max()).date() - timedelta(days=REVISION_DAYS))
        return None

    def _in_period(self, source_id: str, code: str, period: DateRange) -> pd.DataFrame:
        df = self._levels(source_id, code)
        if df.empty:
            return df
        dates = pd.to_datetime(df["date"])
        return df[(dates >= pd.Timestamp(period.start)) & (dates <= pd.Timestamp(period.end))]

    def _levels(self, source_id: str, code: str) -> pd.DataFrame:
        df = self._store.read(source_id, [code])
        return df[df["metric"] == "level"] if not df.empty else df


def _latest_names(df: pd.DataFrame) -> pd.Series:
    """entity_id → latest name: a renamed player (e.g. an insurer after a merger) keeps one label."""
    named = df[(df["entity_id"].fillna("") != "") & df["entity_name"].notna()].sort_values("date")
    return named.groupby("entity_id")["entity_name"].last()


def _monthly(df: pd.DataFrame, freq: Frequency, kind: SeriesKind) -> pd.DataFrame:
    """Entity and total rows of a sub-monthly series as calendar-month rows (month-end dates). Months after the last
    complete month of the total are dropped, so every entity covers the same months."""
    df = df[df["freq"] == freq.value] if "freq" in df.columns and (df["freq"] == freq.value).any() else df
    frames = []
    for entity_id, rows in df.groupby(df["entity_id"].fillna(""), sort=False):
        values = pd.Series(rows["value"].astype(float).to_numpy(), index=pd.to_datetime(rows["date"]))
        monthly = to_monthly(values, freq, kind=kind)
        named = rows["entity_name"].dropna()
        frames.append(pd.DataFrame({
            "entity_id": entity_id, "entity_name": named.iloc[-1] if len(named) else None,
            "date": monthly.index.date, "value": monthly.to_numpy(),
            "is_proxy": bool(rows["is_proxy"].fillna(False).astype(bool).any()),
        }))  # fmt: skip
    out = pd.concat(frames, ignore_index=True)
    totals = out[out["entity_id"] == ""]
    if not totals.empty:
        out = out[out["date"] <= totals["date"].max()]
    return out.reset_index(drop=True)


def _without_negatives(df: pd.DataFrame, entity: pd.Series, code: str) -> tuple[pd.Series, int]:
    """Entity values with net negative amounts set to zero. Source data can carry them legitimately (e.g. insurance
    premiums net of cancellations), but a share cannot be negative; above a small tolerance of the market total the
    series is not usable for shares (e.g. monthly differences around a merger) and is refused."""
    values = df["value"].astype(float)
    negative = entity & (values < 0)
    if not negative.any():
        return values, 0
    dates = pd.to_datetime(df["date"])
    shortfall = (-values[negative]).groupby(dates[negative]).sum()
    totals = values[~entity].groupby(dates[~entity]).sum().reindex(shortfall.index)
    ratio = (shortfall / totals.abs()).fillna(float("inf"))
    if (ratio > NEGATIVE_TOLERANCE).any():
        worst = ratio.idxmax()
        raise InvalidInput(
            f"{code}: bazı dönemlerde oyuncuların negatif net tutarları pazar toplamının %0,5'ini aşıyor "
            f"(ör. {worst:%Y-%m}); bu seriyle pay hesaplanamaz. Kaynağın birikimli ya da stok niteliğindeki serisini seçin."
        )
    return values.where(~negative, 0.0), int(negative.sum())


class AnalyzeMarketShare:
    def __init__(self, build_panel: BuildPanel, reader: TableReader, source_tables: SourceShareTable | None = None):
        self._build = build_panel
        self._reader = reader
        self._source_tables = source_tables

    def from_file(self, path: Path, config: ShareConfig, sector_id: str | None = None, start: date | None = None) -> dict:
        return self(self._reader.read(path), config, sector_id, start)

    def from_text(self, text: str, fmt: str, config: ShareConfig, sector_id: str | None = None, start: date | None = None) -> dict:
        """Table content held in memory (web panel upload/paste); the reader enforces format and size limits."""
        return self(self._reader.parse(text, fmt), config, sector_id, start)

    def from_records(self, records: list[dict], config: ShareConfig, sector_id: str | None = None, start: date | None = None) -> dict:
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise InvalidInput("records, {entity, date, value[, segment]} nesnelerinden oluşan bir liste olmalıdır")
        if len(records) > MAX_ROWS:
            raise InvalidInput(f"En fazla {MAX_ROWS:,} satır gönderilebilir".replace(",", "."))
        return self(pd.DataFrame.from_records(records), config, sector_id, start)

    def from_source(
        self,
        source_id: str,
        series_id: str,
        config: ShareConfig,
        start: date,
        end: date | None = None,
        sector_id: str | None = None,
        refresh: bool = False,
        kind: str | None = None,
    ) -> dict:
        """Shares of the entities (e.g. bank groups) of a catalog series. `kind` defaults to the catalog's series kind
        (level → stock, flow → flow), the unit to the catalog unit; source and proxy status are added to `meta`."""
        if self._source_tables is None:
            raise InvalidInput("Kaynaktan pazar payı bu yapılandırmada kullanılamıyor")
        table, meta = self._source_tables(source_id, series_id, start, end, refresh)
        series_kind = "flow" if meta["kind"] == SeriesKind.FLOW.value else "stock"
        config = replace(config, kind=kind or series_kind, unit=config.unit or meta["unit"] or "")
        out = self(table, config, sector_id, start)
        out["meta"]["source"] = meta
        if meta.get("aggregated_from"):
            out["issues"].append({"severity": "info", "check": "aggregated_to_monthly",
                                  "message": "Seri ay altı frekansta; paylar takvim aylarına çevrilerek hesaplandı (akım: toplam, stok: ortalama, tamamlanmamış son ay hariç)."})  # fmt: skip
        if meta.get("negative_rows"):
            out["issues"].append({"severity": "info", "check": "negative_values",
                                  "message": f"{meta['negative_rows']} satırda negatif net tutar (iptal, iade) vardı; pay hesabında sıfır kabul edildi."})  # fmt: skip
        if meta.get("fetch_error"):
            out["issues"].append({"severity": "medium", "check": "source_unavailable",
                                  "message": f"Kaynak okunamadı, paylar yerel depodaki verilerle hesaplandı: {meta['fetch_error']}"})  # fmt: skip
        if meta["is_proxy"]:
            out["issues"].append({"severity": "medium", "check": "proxy",
                                  "message": "Seri doğrudan pazar verisi değil, proxy'dir; paylar yaklaşık olarak yorumlanmalıdır."})  # fmt: skip
        return out

    def __call__(self, table: pd.DataFrame, config: ShareConfig, sector_id: str | None = None, start: date | None = None) -> dict:
        panel = None
        skipped: dict[str, str] = {}
        if sector_id:
            sector_id = slug(sector_id, "sector")
            first = start or date(2012, 1, 1)
            result = self._build(sector_id, first, None, save=False)
            panel, skipped = result.panel, result.skipped
        out = analyze(table, config, panel)
        out["meta"]["sector"] = sector_id
        if skipped:
            out["meta"]["panel_skipped"] = skipped
        return out
