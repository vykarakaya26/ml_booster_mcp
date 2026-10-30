"""Port'ların bellek içi sahteleri: use case'ler HTTP/dosya olmadan test edilir."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderNotImplemented
from mlbooster.domain.model import HealthResult, HealthStatus, Source
from mlbooster.domain.observations import empty_observations


class FakeProvider:
    def __init__(self, source: Source, series: dict[str, pd.Series]):
        self.source, self.series, self.calls, self.closed = source, series, [], False

    def list_series(self, query=None):
        return pd.DataFrame({"series_id": list(self.series)})

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        self.calls.append(request)
        frames = []
        for code in request.series_ids:
            s = self.series[code]
            seed = self.source.seed(code)
            frames.append(
                pd.DataFrame(
                    {
                        "source_id": self.source.id, "dataset_id": "x", "series_id": code, "entity_id": "",
                        "entity_name": None, "metric": request.metric, "date": s.index.date, "freq": seed.freq.value,
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
            raise ProviderNotImplemented(f"{source.id} için sağlayıcı henüz yazılmadı")
        provider = FakeProvider(source, self.series)
        self.created.append(provider)
        return provider


class InMemoryObservationStore:
    def __init__(self):
        self.frames: list[pd.DataFrame] = []

    def write(self, observations):
        self.frames.append(observations)

    def read(self, source_id, series_ids=None, as_of=None):
        if not self.frames:
            return empty_observations()
        df = pd.concat(self.frames, ignore_index=True)
        df = df[df["source_id"] == source_id]
        return df[df["series_id"].isin(series_ids)] if series_ids else df


class InMemoryPanelStore:
    def __init__(self):
        self.saved: dict[str, pd.DataFrame] = {}

    def save(self, sector_id, panel):
        self.saved[sector_id] = panel
        return f"memory://{sector_id}"

    def export(self, panel, path):
        return str(path)
