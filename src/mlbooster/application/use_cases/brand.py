"""Brand-first market share: find a name among the entity-level sources, report its position, build its features."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError
from mlbooster.application.ports import (
    CatalogRepository,
    EntityDirectory,
    ObservationStore,
    PanelStore,
    ProviderFactory,
)
from mlbooster.application.use_cases.market_share import AnalyzeMarketShare, SourceShareTable
from mlbooster.application.use_cases.panel import BuildPanel
from mlbooster.application.use_cases.series import FetchSeries
from mlbooster.domain.errors import DomainError, InvalidInput
from mlbooster.domain.model import Catalog, Frequency, SeedSeries, SeriesKind, ShareSource, Source
from mlbooster.domain.services import brand_report as report
from mlbooster.domain.services.brand_features import (
    BRAND_COLUMNS,
    brand_metrics,
    monthly_grid,
    panel_id,
    point_in_time,
)
from mlbooster.domain.services.brand_resolution import (
    AMBIGUOUS,
    NOT_FOUND,
    RESOLVED,
    BrandMatch,
    BrandResolution,
    EntityRecord,
    EntityScan,
    is_placeholder,
    pick,
    resolve,
    sector_hints,
    trends_code,
)
from mlbooster.domain.services.calendar_tr import HOLIDAY_YEARS, uncovered_years
from mlbooster.domain.services.feature_engineering import FeatureConfig, FeatureSpec, generate
from mlbooster.domain.services.market_share import ShareConfig
from mlbooster.domain.validation import brand_choice, brand_query

INDEX_WINDOW_DAYS = 270  # covers the latest published period of every source, quarterly reports included
INDEX_TTL_DAYS = 35  # a player list older than this is read again; the old list stays if the source is down
DEFAULT_YEARS = 5
TRENDS_WINDOW_DAYS = 14
PERCENT_UNITS = {"percent"}  # series published as shares: the values are not amounts


class StoreEntityDirectory:
    """Player lists from the observation store. A source that has never been read is fetched for a short recent
    window, so the first search reads a few files per source and later searches read nothing."""

    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory, fetch: FetchSeries, store: ObservationStore):
        self._catalog = catalog
        self._providers = providers
        self._fetch = fetch
        self._store = store

    def scan(self, refresh: bool = False) -> EntityScan:
        catalog = self._catalog.load()
        records: list[EntityRecord] = []
        skipped: dict[str, str] = {}
        for source_id, codes in self._index(catalog).items():
            source = catalog.source(source_id)
            frame, error = self._players(source, codes, refresh)
            records += _records(source.id, frame)
            if error and frame.empty:
                skipped[source.id] = error
        return EntityScan(tuple(records), skipped)

    def _index(self, catalog: Catalog) -> dict[str, list[str]]:
        ready = self._providers.available()
        out: dict[str, list[str]] = {}
        for sector in catalog.sectors.values():
            for share in sector.market_share_sources:
                if share.series and catalog.source(share.source).provider in ready:
                    known = out.setdefault(share.source, [])
                    known += [code for code in share.index_series if code not in known]
        return out

    def _players(self, source: Source, codes: list[str], refresh: bool) -> tuple[pd.DataFrame, str | None]:
        cached = pd.DataFrame() if refresh else self._levels(source.id, codes)
        if not refresh and _fresh(cached, codes):
            return cached, None
        today = date.today()
        try:
            fetched = self._fetch(FetchRequest.create(source.id, codes, today - timedelta(days=INDEX_WINDOW_DAYS), today), refresh=refresh)
        except (ProviderError, InvalidInput) as exc:
            return self._levels(source.id, codes), str(exc)
        return (self._levels(source.id, codes) if source.storage.observations else fetched), None

    def _levels(self, source_id: str, codes: list[str]) -> pd.DataFrame:
        df = self._store.read(source_id, codes)
        return df[df["metric"] == "level"] if not df.empty else df


def _fresh(cached: pd.DataFrame, codes: list[str]) -> bool:
    if cached.empty or set(codes) - set(cached["series_id"]):
        return False
    last_pull = pd.Timestamp(cached["vintage_ts"].max())
    last_pull = last_pull.tz_localize("UTC") if last_pull.tzinfo is None else last_pull
    return pd.Timestamp(datetime.now(UTC)) - last_pull <= pd.Timedelta(days=INDEX_TTL_DAYS)


def _records(source_id: str, frame: pd.DataFrame) -> list[EntityRecord]:
    if frame.empty:
        return []
    rows = frame[(frame["entity_id"].fillna("") != "") & frame["entity_name"].notna()].sort_values("date", ascending=False)
    out = []
    for entity_id, group in rows.groupby("entity_id", sort=True):
        names = tuple(dict.fromkeys(group["entity_name"]))
        if not is_placeholder(names[0]):
            out.append(EntityRecord(source_id, str(entity_id), names[0], names, tuple(sorted(set(group["series_id"]))), str(group["date"].iloc[0])))
    return out


def _sector_of(catalog: Catalog) -> dict[str, str]:
    """Source id → the sector whose market share list holds its entity series."""
    out: dict[str, str] = {}
    for sector in catalog.sectors.values():
        for share in sector.market_share_sources:
            if share.series:
                out.setdefault(share.source, sector.id)
    return out


class ResolveBrand:
    """Text → the brand, the candidates to choose from, or a plain statement of what is available instead."""

    def __init__(self, catalog: CatalogRepository, directory: EntityDirectory, fetch: FetchSeries | None = None):
        self._catalog = catalog
        self._directory = directory
        self._fetch = fetch

    def __call__(self, query: str, refresh: bool = False, check_trends: bool = False) -> dict:
        resolution, scan = self.resolution(query, refresh=refresh)
        return self.describe(resolution, scan, check_trends)

    def resolution(self, query: str, choice: str | None = None, refresh: bool = False) -> tuple[BrandResolution, EntityScan]:
        text = brand_query(query)
        scan = self._directory.scan(refresh)
        catalog = self._catalog.load()
        sector_of = _sector_of(catalog)
        if choice is not None:
            return pick(text, brand_choice(choice), scan, catalog.brands, sector_of), scan
        return resolve(text, scan, catalog.brands, sector_of), scan

    def describe(self, resolution: BrandResolution, scan: EntityScan, check_trends: bool = False) -> dict:
        out = {
            "query": resolution.query, "status": resolution.status,
            "matches": [m.as_dict() for m in resolution.matches], "others": [m.as_dict() for m in resolution.others],
            "total": resolution.total, "scanned_entities": len(scan.records), "skipped_sources": dict(scan.skipped),
        }  # fmt: skip
        if resolution.status == AMBIGUOUS:
            out["message"] = f"'{resolution.query}' birden fazla oyuncuyla eşleşiyor ya da eşleşme kesin değil; devam etmek için bir aday seçin."
        elif resolution.status == NOT_FOUND:
            out.update(self._limits(resolution, scan, check_trends))
        return out

    def _limits(self, resolution: BrandResolution, scan: EntityScan, check_trends: bool) -> dict:
        catalog = self._catalog.load()
        hints = sector_hints(resolution.query, resolution.known, catalog.brands)
        sectors = [{"sector": s, "name": catalog.sector(s).name, "reason": reason} for s, reason in hints]
        message = f"'{resolution.query}' oyuncu düzeyinde veri veren kaynaklarda bulunamadı; bu nedenle pazar payı hesaplanmadı."
        if resolution.known is not None and resolution.known.note:
            message += f" {resolution.known.note}"
        if scan.skipped:
            message += f" Şu kaynaklar okunamadığı için sonuç kesin değil: {', '.join(sorted(scan.skipped))}."
        return {
            "message": message, "sector_candidates": sectors,
            "available": _alternatives(catalog, [s["sector"] for s in sectors]) + [self._trends(resolution.query, check_trends)]
            + [{"kind": "user_table", "message": "Kendi pazar payı tablonuzu (entity, date, value) yükleyerek analiz yapabilirsiniz."}],
        }  # fmt: skip

    def _trends(self, query: str, check: bool) -> dict:
        code = trends_code(query, self._catalog.load().brands)
        out = {"kind": "google_trends", "series": code, "is_proxy": True, "listed": None,
               "message": "Google Trends yalnızca Türkiye Top 25 listelerine giren terimleri içerir; değer pazar payı değil, arama ilgisidir."}  # fmt: skip
        if not check or self._fetch is None or code is None:
            return out
        today = date.today()
        try:
            rows = self._fetch(FetchRequest.create("bq_trends", [code], today - timedelta(days=TRENDS_WINDOW_DAYS), today))
        except DomainError as exc:
            return {**out, "message": f"{out['message']} Kontrol edilemedi: {exc}"}
        return {**out, "listed": not rows.empty}


def _alternatives(catalog: Catalog, sector_ids: list[str]) -> list[dict]:
    out = []
    for sector_id in sector_ids:
        sector = catalog.sector(sector_id)
        out.append({"kind": "sector_panel", "sector": sector.id,
                    "message": f"{sector.name} sektörünün makro feature paneli hazırlanabilir."})  # fmt: skip
        for share in sector.market_share_sources:
            if not share.series:
                out.append({"kind": "share_source", "sector": sector.id, "source": share.source, "is_proxy": share.is_proxy,
                            "message": f"{catalog.source(share.source).name}: {share.metric}"})  # fmt: skip
    return out


@dataclass(frozen=True)
class SeriesPlan:
    """A series of the brand's source and the labels of the brand in it."""

    seed: SeedSeries
    focus: str
    siblings: tuple[str, ...]  # other entities of the same brand: they are not competitors


@dataclass(frozen=True)
class BrandPeriod:
    start: date
    end: date | None


class _BrandSeries:
    """Which series of its sector a resolved brand appears in."""

    def __init__(self, catalog: CatalogRepository, tables: SourceShareTable):
        self._catalog = catalog
        self._tables = tables

    def share_source(self, match: BrandMatch) -> ShareSource:
        sector = self._catalog.load().sector(match.sector)
        return next(s for s in sector.market_share_sources if s.source == match.source and s.series)

    def period(self, share: ShareSource, start: date | None, end: date | None) -> BrandPeriod:
        today = date.today()
        return BrandPeriod(start or date(today.year - (share.history_years or DEFAULT_YEARS), 1, 1), end)

    def plan(self, match: BrandMatch, period: BrandPeriod, refresh: bool) -> tuple[list[SeriesPlan], dict[str, str]]:
        """Series that list the brand; the others are returned with the reason. The series of the source are fetched
        in one pass; if that fails, what is already cached is used."""
        share = self.share_source(match)
        source = self._catalog.load().source(match.source)
        skipped: dict[str, str] = {}
        try:
            self._tables.prefetch(source.id, list(share.series), period.start, period.end, refresh)
        except (ProviderError, InvalidInput) as exc:
            skipped["fetch"] = str(exc)
        plans = []
        for code in share.series:
            labels = self._tables.labels(source.id, code, period.start, period.end)
            found = [labels[e] for e in match.entity_ids if e in labels]
            seed = source.seed(code)
            if seed is None:
                skipped[code] = "Katalogda seri tanımı yok"
            elif not found:
                skipped[code] = "Oyuncu bu seride yer almıyor"
            else:
                plans.append(SeriesPlan(seed, found[0], tuple(found[1:])))
        return plans, skipped


def _config(plan: SeriesPlan, trend_years: int = 3) -> ShareConfig:
    measure = "share" if plan.seed.unit in PERCENT_UNITS else "value"
    return ShareConfig(focus=plan.focus, measure=measure, trend_years=trend_years)


def _display_names(catalog: Catalog, source_id: str, scan: EntityScan) -> dict[str, str]:
    """Source label → catalog brand name, for the players of one source."""
    brand_of: dict[str, str] = {}
    for entry in catalog.brands.entries:  # the first entry of an entity is the widest brand (Türk Telekom before TT Mobil)
        if len(entry.targets) == 1 and entry.targets[0].source == source_id:
            for entity in entry.targets[0].entities:
                brand_of.setdefault(entity, entry.name)
    return {name: brand_of[r.entity_id] for r in scan.records if r.source == source_id and r.entity_id in brand_of for name in r.names}


def _source_notes(catalog: Catalog, match: BrandMatch, is_proxy: bool) -> list[dict]:
    return [report.source_note(catalog.source(match.source), is_proxy)]


class AnalyzeBrand:
    """Position, concentration, competitors and movers of a brand across the series of its sector. A text that does not
    lead to one brand returns the resolution (candidates or limits); no share is produced for it."""

    def __init__(self, catalog: CatalogRepository, resolver: ResolveBrand, market_share: AnalyzeMarketShare, tables: SourceShareTable):
        self._catalog = catalog
        self._resolver = resolver
        self._share = market_share
        self._tables = tables
        self._series = _BrandSeries(catalog, tables)

    def __call__(
        self,
        query: str,
        choice: str | None = None,
        start: date | None = None,
        end: date | None = None,
        refresh: bool = False,
        trend_years: int = 3,
    ) -> dict:
        resolution, scan = self._resolver.resolution(query, choice, refresh)
        if resolution.status != RESOLVED:
            return self._resolver.describe(resolution, scan)
        match = resolution.match
        share = self._series.share_source(match)
        period = self._series.period(share, start, end)
        plans, skipped = self._series.plan(match, period, refresh)
        results = self._analyses(match, plans, period, trend_years, skipped)
        if not results:
            return self._no_data(resolution, match, period, skipped)
        segments = self._segments(match, share, results[0][0], period)
        return self._assemble(resolution, scan, share, results, segments, skipped, period)

    def _analyses(self, match: BrandMatch, plans: list[SeriesPlan], period: BrandPeriod, trend_years: int, skipped: dict) -> list[tuple[SeriesPlan, dict]]:
        out = []
        for plan in plans:
            try:
                result = self._share.from_source(match.source, plan.seed.code, _config(plan, trend_years), period.start, period.end)
            except (ProviderError, InvalidInput) as exc:
                skipped[plan.seed.code] = str(exc)
                continue
            player = next(p for p in result["players"] if p["entity"] == result["meta"]["focus"])
            if player["share"]:
                out.append((plan, result))
            else:
                skipped[plan.seed.code] = "Oyuncunun son dönemde bu seride hacmi yok"
        return out

    def _segments(self, match: BrandMatch, share: ShareSource, primary: SeriesPlan, period: BrandPeriod) -> dict | None:
        """Share analysis over the series that split the market: gives the segment mix, the overlap with other players
        and the shift-share of the brand. Segments the brand is not active in stay in, they weigh the market."""
        tables = []
        for code in share.segments:
            try:
                tables.append(self._tables(match.source, code, period.start, period.end)[0].assign(segment=code))
            except (ProviderError, InvalidInput):
                continue
        if len(tables) < 2:
            return None
        try:
            kind = "flow" if primary.seed.kind is SeriesKind.FLOW else "stock"
            return self._share(pd.concat(tables, ignore_index=True), ShareConfig(focus=primary.focus, kind=kind))
        except InvalidInput:
            return None

    def _no_data(self, resolution: BrandResolution, match: BrandMatch, period: BrandPeriod, skipped: dict) -> dict:
        return {
            "query": resolution.query, "status": "no_data", "brand": match.as_dict(), "skipped_series": skipped,
            "message": f"{match.name} kaynakta bulundu; ancak {period.start} sonrası için pay hesaplanabilecek veri alınamadı.",
        }  # fmt: skip

    def _assemble(
        self,
        resolution: BrandResolution,
        scan: EntityScan,
        share: ShareSource,
        results: list[tuple[SeriesPlan, dict]],
        segments: dict | None,
        skipped: dict,
        period: BrandPeriod,
    ) -> dict:
        catalog = self._catalog.load()
        match = resolution.match
        sector = catalog.sector(match.sector)
        summaries = [report.series_summary(r, p.seed.code, p.seed.name, set(p.siblings)) for p, r in results]
        for summary, (_, result) in zip(summaries, results, strict=True):
            summary["is_proxy"] = share.is_proxy or bool(result["meta"]["source"]["is_proxy"])
        primary_plan, primary = results[0]
        exclude = {label for p, _ in results for label in (p.focus, *p.siblings)}
        is_proxy = any(s["is_proxy"] for s in summaries)
        display = _display_names(catalog, match.source, scan)
        return {
            "query": resolution.query, "status": RESOLVED, "brand": match.as_dict(),
            "others": [m.as_dict() for m in resolution.others],
            "sector": {"id": sector.id, "name": sector.name, "coverage": share.metric, "notes": share.notes},
            "headline": report.headline(match.name, sector.name, summaries[0]),
            "position": summaries[0], "series": summaries,
            "concentration": summaries[0]["concentration"],
            "competitors": report.merge_competitors([(p.seed.code, r) for p, r in results], segments, exclude, display),
            "movers": report.movers(primary, set(primary_plan.siblings), display),
            "share_history": primary["shares"],
            "segments": segments["segments"] if segments else None,
            "period": {"requested_start": period.start.isoformat(), "first": primary["meta"]["first"], "last": primary["meta"]["last"]},
            "sources": _source_notes(catalog, match, is_proxy), "is_proxy": is_proxy,
            "issues": report.merged_issues([(p.seed.code, r) for p, r in results]),
            "skipped_series": skipped,
        }  # fmt: skip


@dataclass
class BrandFeatures:
    brand: dict
    panel: pd.DataFrame = field(default_factory=pd.DataFrame)
    specs: list[FeatureSpec] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    is_proxy: bool = False
    location: str | None = None
    withheld: list[str] = field(default_factory=list)  # columns not written to disk: their source's terms forbid storing


class BuildBrandFeatures:
    """Monthly point-in-time panel of a resolved brand plus the macro features of its sector. The full brand metrics
    come from the primary series; the other series add their share only."""

    def __init__(self, catalog: CatalogRepository, resolver: ResolveBrand, tables: SourceShareTable, build_panel: BuildPanel, panels: PanelStore):
        self._catalog = catalog
        self._resolver = resolver
        self._tables = tables
        self._build = build_panel
        self._panels = panels
        self._series = _BrandSeries(catalog, tables)

    def __call__(
        self,
        query: str,
        choice: str | None = None,
        start: date | None = None,
        end: date | None = None,
        refresh: bool = False,
        save: bool = True,
    ) -> BrandFeatures | dict:
        """Returns the resolution as a dict if the text does not lead to one brand."""
        resolution, scan = self._resolver.resolution(query, choice, refresh)
        if resolution.status != RESOLVED:
            return self._resolver.describe(resolution, scan)
        match = resolution.match
        catalog = self._catalog.load()
        share = self._series.share_source(match)
        period = self._series.period(share, start, end)
        plans, skipped = self._series.plan(match, period, refresh)
        primary, extra, is_proxy = self._metrics(catalog, match, plans, period, skipped)
        if primary is None:
            raise InvalidInput(f"{match.name} için {period.start} sonrasında feature üretilecek pay verisi yok")
        macro = self._build(match.sector, period.start, period.end, save=False)
        skipped.update(macro.skipped)
        last = pd.Timestamp(period.end or date.today()) + pd.offsets.MonthEnd(0)
        grid = monthly_grid([primary, *extra.values(), macro.panel], pd.Timestamp(period.start), last)
        joined = pd.concat([primary.reindex(grid), macro.panel.reindex(grid)], axis=1)
        sector = catalog.sector(match.sector)
        panel, specs, recipe_skips = generate(joined, sector.feature_recipes, FeatureConfig(), sector.events)
        skipped.update(recipe_skips)
        specs = [_described(s) for s in specs]
        for name, series in extra.items():
            panel[name] = series.reindex(grid)
            specs.append(FeatureSpec(name, "base", (name,), f"Markanın {name.removeprefix('marka_pay_')} serisindeki payı (%)"))
        panel.index.name = "date"
        result = BrandFeatures(match.as_dict(), panel, specs, skipped, _notes(grid), _source_notes(catalog, match, is_proxy), is_proxy)
        if save:
            result.withheld = _withheld(catalog, sector, specs)
            result.location = self._panels.save(panel_id(match.name, match.id), panel.drop(columns=result.withheld))
        return result

    def _metrics(self, catalog: Catalog, match: BrandMatch, plans: list[SeriesPlan], period: BrandPeriod, skipped: dict) -> tuple[pd.DataFrame | None, dict[str, pd.Series], bool]:
        source = catalog.source(match.source)
        primary: pd.DataFrame | None = None
        extra: dict[str, pd.Series] = {}
        is_proxy = self._series.share_source(match).is_proxy
        for plan in plans:
            code = plan.seed.code
            try:
                table, meta = self._tables(source.id, code, period.start, period.end)
                metrics = brand_metrics(table, replace(_config(plan), kind="flow" if plan.seed.kind is SeriesKind.FLOW else "stock"), plan.siblings)
            except (ProviderError, InvalidInput) as exc:
                skipped[code] = str(exc)
                continue
            if not (metrics["marka_pay"].iloc[-1] > 0):
                skipped[code] = "Oyuncunun son dönemde bu seride hacmi yok"
                continue
            is_proxy = is_proxy or bool(meta["is_proxy"])
            monthly = point_in_time(metrics, Frequency(plan.seed.freq), source.lag_for(code))
            if primary is None:
                primary = monthly
            else:
                extra[f"marka_pay_{_suffix(code, source.id)}"] = monthly["marka_pay"]
        return primary, extra, is_proxy


def _suffix(code: str, source_id: str) -> str:
    return code.lower().removeprefix(f"{source_id}.").replace(".", "_")


def _described(spec: FeatureSpec) -> FeatureSpec:
    return replace(spec, family="brand", description=BRAND_COLUMNS[spec.name]) if spec.name in BRAND_COLUMNS else spec


def _notes(grid: pd.DatetimeIndex) -> list[str]:
    notes = ["Her satır yalnızca o ayın sonuna kadar yayımlanmış veriyi içerir; rakip_pay her dönemin kendi sıralamasından hesaplanır."]
    if missing := uncovered_years(grid):
        notes.append(f"Dini bayram tablosu {HOLIDAY_YEARS[0]}–{HOLIDAY_YEARS[1]} yıllarını kapsıyor; {', '.join(map(str, missing))} yıllarında takvim kolonları boş.")
    return notes


def _withheld(catalog: Catalog, sector, specs: list[FeatureSpec]) -> list[str]:
    """Columns built from a source whose terms forbid keeping its data (BKM). Specs come inputs first."""
    held = {f.name for f in sector.macro_features if not catalog.source(f.source).storage.observations}
    out = []
    for spec in specs:
        if spec.name in held or held & set(spec.inputs):
            held.add(spec.name)
            out.append(spec.name)
    return out
