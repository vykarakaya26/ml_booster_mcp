"""In-memory fakes of the ports: use cases are tested without HTTP/files."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderNotImplemented
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
