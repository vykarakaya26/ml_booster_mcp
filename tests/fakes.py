"""In-memory fakes of the ports: use cases are tested without HTTP/files."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from mlbooster.application.dto import FetchRequest, PanelResult
from mlbooster.application.errors import ProviderNotImplemented, ProviderUnavailable
from mlbooster.application.use_cases.brand import AnalyzeBrand, BuildBrandFeatures, ResolveBrand, StoreEntityDirectory
from mlbooster.application.use_cases.market_share import AnalyzeMarketShare, SourceShareTable
from mlbooster.application.use_cases.series import FetchSeries
from mlbooster.domain.model import HealthResult, HealthStatus, Source
from mlbooster.domain.observations import KEY_COLUMNS, empty_observations


class FakeProvider:
    def __init__(self, source: Source, series: dict[str, pd.Series]):
        self.source, self.series, self.calls, self.closed = source, series, [], False

    def list_series(self, query=None):
        return pd.DataFrame({"series_id": list(self.series)})

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        self.calls.append(request)
        frames = []
        for code in request.series_ids:
            if code not in self.series:  # like the real provider: returns empty if there is no data
                continue
            s = self.series[code]
            seed = self.source.seed(code)
            frames.append(
                pd.DataFrame(
                    {
                        "source_id": self.source.id, "dataset_id": "x", "series_id": code, "entity_id": "",
                        "entity_name": None, "metric": request.metric, "date": s.index.date, "freq": seed.freq.value if seed else "M",
                        "value": s.to_numpy(), "unit": None, "currency": None, "is_proxy": False,
                        "vintage_ts": datetime.now(UTC), "publication_lag_days": self.source.lag_for(code),
                    }
                )  # fmt: skip
            )
        return pd.concat(frames, ignore_index=True) if frames else empty_observations()

    def healthcheck(self):
        return HealthResult(self.source.id, HealthStatus.OK, "fake")

    def close(self):
        self.closed = True


class FakeProviderFactory:
    def __init__(self, series: dict[str, pd.Series]):
        self.series, self.created = series, []

    def available(self):
        return {"evds3"}

    def create(self, source):
        if source.provider not in self.available():
            raise ProviderNotImplemented(f"{source.id} için provider henüz uygulanmadı")
        provider = FakeProvider(source, self.series)
        self.created.append(provider)
        return provider


class InMemoryObservationStore:
    def __init__(self):
        self.frames: list[pd.DataFrame] = []
        self.requested: dict[tuple[str, str, str], object] = {}

    def record_requested_start(self, source_id, series_ids, metric, start):
        for code in series_ids:
            key = (source_id, code, metric)
            self.requested[key] = min(start, self.requested.get(key, start))

    def requested_start(self, source_id, series_id, metric):
        return self.requested.get((source_id, series_id, metric))

    def write(self, observations):
        self.frames.append(observations)

    def read(self, source_id, series_ids=None, as_of=None):
        if not self.frames:
            return empty_observations()
        df = pd.concat(self.frames, ignore_index=True)
        df = df[df["source_id"] == source_id]
        # like the Parquet store: the latest vintage per observation key
        df = df.sort_values("vintage_ts", kind="stable").drop_duplicates(KEY_COLUMNS, keep="last")
        return df[df["series_id"].isin(series_ids)] if series_ids else df


class InMemoryPanelStore:
    def __init__(self):
        self.saved: dict[str, pd.DataFrame] = {}

    def save(self, sector_id, panel):
        self.saved[sector_id] = panel
        return f"memory://{sector_id}"

    def export(self, panel, path):
        return str(path)


# Bank-group style series: a total under an empty entity_id plus entity rows (like the BDDK provider).
GROUPS = {"10009": ("Mevduat-Kamu", 40.0), "10008": ("Mevduat-Yerli Özel", 35.0), "10003": ("Katılım", 25.0)}


class EntityProvider:
    """Provider fake that returns a sector total (empty entity_id) plus bank group rows for every month."""

    def __init__(self, source, calls, proxy=False, groups=GROUPS):
        self.source, self.calls, self.proxy, self.groups = source, calls, proxy, groups

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        self.calls.append(request)
        months = pd.date_range(request.period.start, request.period.end, freq="ME")
        rows = []
        for i, d in enumerate(months):
            growth = 1 + i / 100
            parts = {code: (name, base * growth * (1 + (code == "10003") * i / 200)) for code, (name, base) in self.groups.items()}
            rows.append(("", "Sektör", d.date(), sum(v for _, v in parts.values())))
            rows += [(code, name, d.date(), v) for code, (name, v) in parts.items()]
        if not rows:
            return empty_observations()
        df = pd.DataFrame(rows, columns=["entity_id", "entity_name", "date", "value"])
        return df.assign(
            source_id="bddk", dataset_id="aylik_bulten_1", series_id=request.series_ids[0], metric="level", freq="M",
            unit="million_try", currency="TRY", is_proxy=self.proxy, vintage_ts=datetime.now(UTC), publication_lag_days=30,
        )  # fmt: skip

    def close(self):
        pass


class EntityFactory:
    def __init__(self, **kwargs):
        self.calls: list[FetchRequest] = []
        self.kwargs = kwargs

    def available(self):
        return {"bddk", "evds3"}

    def create(self, source):
        return EntityProvider(source, self.calls, **self.kwargs)


# Brand flow: players of the entity-level sources; some series list only some of them.
PLAYERS = {
    "odmd": {"TOGG": ("TOGG", 20), "RENAULT": ("RENAULT", 50), "FIAT": ("FIAT", 30)},
    "tbb": {"30": ("Türkiye Garanti Bankası A.Ş.", 40), "8": ("Akbank T.A.Ş.", 35), "3": ("Türkiye Cumhuriyeti Ziraat Bankası A.Ş.", 25)},
    "tsb": {"1005": ("Anadolu Anonim Türk Sigorta Şirketi", 30), "3004": ("Anadolu Hayat Emeklilik AŞ", 30), "1020": ("Türkiye Sigorta AŞ", 40)},
    "epdk": {"OPET_PETROLCULUK": ("OPET PETROLCÜLÜK", 30), "PETROL_OFISI": ("PETROL OFİSİ", 40), "SHELL_TURCAS": ("SHELL & TURCAS", 30)},
    "btk": {"TURKCELL": ("Turkcell", 40), "VODAFONE": ("Vodafone", 30), "TT_MOBIL": ("TT Mobil", 30), "SUPERONLINE": ("Superonline", 15), "DIGER": ("Diğer", 5)},
    "bddk": {"10003": ("Katılım", 10), "10009": ("Mevduat-Kamu", 90)},
}
FIRST = date(2018, 1, 1)  # sources have no rows before this date
ONLY_IN = {"EPDK.LPG": {"PETROL_OFISI", "SHELL_TURCAS"}, "ODMD.HAFIF_TICARI": {"RENAULT", "FIAT"}, "BTK.SGB": {"SUPERONLINE", "DIGER"}}


class BrandProvider:
    def __init__(self, source, calls, proxy):
        self.source, self.calls, self.proxy = source, calls, proxy

    def fetch(self, request):
        self.calls.append(request)
        rows = []
        for code in request.series_ids:
            seed = self.source.seed(code)
            rule = "QE" if seed.freq.value == "Q" else "ME"
            allowed = next((v for k, v in ONLY_IN.items() if code.startswith(k)), None)
            players = {k: v for k, v in PLAYERS[self.source.id].items() if allowed is None or k in allowed}
            for i, day in enumerate(pd.date_range(max(request.period.start, FIRST), request.period.end, freq=rule)):
                parts = {k: (name, base * (1 + i / (60 if k == next(iter(players)) else 200))) for k, (name, base) in players.items()}
                total = sum(v for _, v in parts.values())
                scale = 100 / total if seed.unit == "percent" else 1
                rows.append((code, "", "Toplam", day.date(), total * scale))
                rows += [(code, k, name, day.date(), v * scale) for k, (name, v) in parts.items()]
        if not rows:
            return empty_observations()
        df = pd.DataFrame(rows, columns=["series_id", "entity_id", "entity_name", "date", "value"])
        return df.assign(
            source_id=self.source.id, dataset_id="x", metric="level", freq=[self.source.seed(c).freq.value for c in df["series_id"]],
            unit=None, currency=None, is_proxy=self.proxy, vintage_ts=datetime.now(UTC), publication_lag_days=self.source.publication_lag_days,
        )  # fmt: skip

    def close(self):
        pass


class BrandFactory:
    def __init__(self, proxy=False, down=()):
        self.calls, self.proxy, self.down = [], proxy, set(down)

    def available(self):
        return set(PLAYERS)

    def create(self, source):
        if source.id in self.down or source.id not in PLAYERS:
            raise ProviderUnavailable(f"{source.id} yanıt vermedi")
        return BrandProvider(source, self.calls, self.proxy)


class InMemoryEvidenceStore:
    def __init__(self):
        self.items, self.rejected = {}, {}

    def add(self, evidence):
        if evidence.id in self.items:
            return False
        self.items[evidence.id] = evidence
        return True

    def list(self, brand_keys=None):
        return [e for e in self.items.values() if brand_keys is None or e.brand_key in brand_keys]

    def remove(self, evidence_id, reason, at):
        item = self.items.pop(evidence_id, None)
        if item is not None:
            self.rejected[evidence_id] = reason
        return item

    def rejection(self, evidence_id):
        return self.rejected.get(evidence_id)


class StaticCatalog:
    def __init__(self, catalog):
        self.catalog = catalog

    def load(self):
        return self.catalog


class StubPanel:
    def __init__(self, panel=None, skipped=None):
        self.panel, self.skipped, self.calls = panel, skipped or {}, []

    def __call__(self, sector_id, start, end, save=True):
        self.calls.append((sector_id, start, save))
        panel = self.panel if self.panel is not None else pd.DataFrame()
        return PanelResult(sector_id, panel, self.skipped)


class BrandWorld:
    def __init__(self, catalog, panel=None, **kwargs):
        self.factory, self.store, self.panels = BrandFactory(**kwargs), InMemoryObservationStore(), InMemoryPanelStore()
        repo = StaticCatalog(catalog)
        fetch = FetchSeries(repo, self.factory, self.store)
        self.tables = SourceShareTable(repo, fetch, self.store)
        self.directory = StoreEntityDirectory(repo, self.factory, fetch, self.store)
        self.resolve = ResolveBrand(repo, self.directory, fetch)
        self.analyze = AnalyzeBrand(repo, self.resolve, AnalyzeMarketShare(None, None, self.tables), self.tables)
        self.panel = panel or StubPanel()
        self.features = BuildBrandFeatures(repo, self.resolve, self.tables, self.panel, self.panels)
