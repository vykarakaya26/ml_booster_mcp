"""TÜİK provider: JSON-stat data from the Veri Portalı data browser (keyless, anonymous).

Live findings 2026-11-02 (CLAUDE.md source rule 2, strategy chain):
- veriportali.tuik.gov.tr/api/... (portal JSON) answers non-browser clients with 403 "Erişim engellendi";
  we do not work around that check.
- The portal's "Tablolar ve Grafikler" entries open the data browser at databrowser2.tuik.gov.tr, whose
  public JSON API serves the same tables: GET `<dataset>/structure` lists the dimensions in key order,
  POST `<dataset>/data` takes the filter (criteria) as a JSON body and returns JSON-stat 2.0.
- Legacy istab Excel links (data.tuik.gov.tr/Bulten/DownloadIstatistikselTablo) now redirect to the portal
  SPA (HTML), so there is no Excel fallback left; MEDAS (biruni) is a stateful ZK UI, not an API.
- nsiws.tuik.gov.tr (SDMX) answers anonymous requests with 401; it is not used.

The catalog `remote` field is `<agency,dataflow,version>/<key>`, where the key lists one code per non-time
dimension in the order of the dataset structure (SDMX style), e.g. `TR,DF_TUFE_SDMX_TT10,1.0/TR.M.TUFE.1.2025.0.F_TFE`.
Requests go only to the host pinned below; the catalog base_url must point to it.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlsplit

import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.domain.periods import parse_period as parse_domain_period
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

HOST = "databrowser2.tuik.gov.tr"
API_PATH = "/api/core/nodes/1/datasets/"
_LOOKBACK_DAYS = {"M": 400, "Q": 800, "A": 2200}
_STALENESS_MARGIN_DAYS = 15  # release dates shift around public holidays


def parse_period(raw: str) -> date:
    if parsed := parse_domain_period(raw):
        return parsed[0]
    raise UnexpectedResponse(f"Tanınmayan TÜİK dönemi: {raw.strip()[:30]!r}")


def _fold(text: str) -> str:
    return text.replace("İ", "i").casefold()  # str.casefold turns 'İ' into 'i' + combining dot


def split_remote(remote: str) -> tuple[str, list[str]]:
    dataflow, _, key = remote.partition("/")
    if dataflow.count(",") != 2 or not key:
        raise InvalidInput(f"TÜİK remote alanı '<kurum,veri seti,sürüm>/<anahtar>' biçiminde olmalıdır: {remote!r}")
    return dataflow, key.split(".")


def select_jsonstat(doc: dict, key: dict[str, str], time_dim: str) -> list[tuple[str, float]]:
    """(period, value) pairs of the single series `key` (dimension → code) in a JSON-stat 2.0 dataset."""
    ids, sizes = doc.get("id"), doc.get("size")
    if not isinstance(ids, list) or not isinstance(sizes, list) or len(ids) != len(sizes):
        raise UnexpectedResponse("TÜİK yanıtı JSON-stat biçiminde değil (id/size)")
    if not ids:
        return []  # no observation in the requested period
    if set(ids) != {*key, time_dim}:
        raise UnexpectedResponse(f"TÜİK veri setinin boyutları değişmiş: {ids}")
    positions: list[list[str]] = []
    for dim in ids:
        index = doc["dimension"][dim]["category"]["index"]
        positions.append(sorted(index, key=index.get) if isinstance(index, dict) else list(index))
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    offset = 0
    for i, dim in enumerate(ids):
        if dim == time_dim:
            continue
        if key[dim] not in positions[i]:
            return []  # the filter matched no series
        offset += positions[i].index(key[dim]) * strides[i]
    values = doc.get("value") or {}
    t = ids.index(time_dim)
    rows = []
    for j, period in enumerate(positions[t]):
        flat = offset + j * strides[t]
        raw = values.get(str(flat)) if isinstance(values, dict) else (values[flat] if flat < len(values) else None)
        if raw is None or raw == "":
            continue
        try:
            rows.append((period, round(float(raw), 10)))  # strip binary noise such as 8.200000000000001
        except (TypeError, ValueError):
            raise UnexpectedResponse(f"TÜİK yanıtında sayısal olmayan değer: {str(raw)[:20]!r}") from None
    return rows


class TuikProvider:
    def __init__(
        self,
        source: Source,
        *,
        http: SecureHttpClient,
        secrets: EnvSecretProvider | None = None,
        raw_store: RawStore | None = None,
    ):
        parts = urlsplit(source.base_url)
        if parts.scheme != "https" or parts.hostname != HOST:
            raise ProviderError(f"{source.id} provider'ı yalnızca https://{HOST} ile çalışır")
        self.source = source
        self._base = f"https://{HOST}{API_PATH}"
        self._http = http
        self._raw_store = raw_store
        self._structures: dict[str, tuple[list[str], str]] = {}

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is None or not seed.remote:
            known = ", ".join(s.code for s in self.source.series)
            raise InvalidInput(f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}")
        return seed

    def _url(self, dataflow: str, endpoint: str) -> str:
        return f"{self._base}{quote(dataflow, safe=',.-_')}/{endpoint}"

    def _json(self, response: HttpResponse) -> dict:
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"TÜİK {response.status} yönlendirmesi döndürdü: {response.url}")
        if response.status != 200:
            raise ProviderError(f"TÜİK HTTP {response.status}: {response.url}")
        if "json" not in response.content_type or response.content.lstrip()[:1] == b"<":
            raise UnexpectedResponse(
                f"TÜİK JSON yerine başka bir içerik döndürdü ({response.content_type[:40]}): {response.url}"
            )
        try:
            doc = json.loads(response.content)
        except ValueError:
            raise UnexpectedResponse(f"TÜİK yanıtı çözümlenemedi: {response.url}") from None
        if not isinstance(doc, dict):
            raise UnexpectedResponse(f"TÜİK yanıtı beklenen nesne biçiminde değil: {response.url}")
        if self._raw_store is not None:
            self._raw_store.save(self.source.id, response.url, response.content, "application/json")
        return doc

    def _structure(self, dataflow: str) -> tuple[list[str], str]:
        """Non-time dimension ids in key order and the time dimension id (cached per dataflow)."""
        if dataflow not in self._structures:
            doc = self._json(self._http.get(self._url(dataflow, "structure"), headers={"Accept": "application/json"}))
            time_dim = doc.get("timeDimension")
            try:
                dims = [c["id"] for c in doc["criteria"]]
            except (KeyError, TypeError):
                raise UnexpectedResponse(f"TÜİK veri seti yapısında boyut listesi yok: {dataflow}") from None
            if not isinstance(time_dim, str) or time_dim not in dims:
                raise UnexpectedResponse(f"TÜİK veri setinde zaman boyutu bulunamadı: {dataflow}")
            self._structures[dataflow] = ([d for d in dims if d != time_dim], time_dim)
        return self._structures[dataflow]

    def _observations(self, seed: SeedSeries, start: date, end: date) -> list[tuple[str, float]]:
        dataflow, codes = split_remote(seed.remote or "")
        dims, time_dim = self._structure(dataflow)
        if len(codes) != len(dims):
            raise UnexpectedResponse(
                f"{seed.code}: katalogdaki anahtar {len(codes)} boyutlu, TÜİK veri seti {len(dims)} boyutlu ({'.'.join(dims)})"
            )
        key = dict(zip(dims, codes, strict=True))
        criteria = [{"id": d, "type": "CodeValues", "filterValues": [c]} for d, c in key.items()]
        criteria.append({"id": time_dim, "type": "TimeRange", "from": start.isoformat(), "to": end.isoformat()})
        response = self._http.post_json(self._url(dataflow, "data"), criteria, headers={"Accept": "application/json"})
        return select_jsonstat(self._json(response), key, time_dim)

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        rows = [
            (s.code, s.name, s.freq.value, None, None, s.remote)
            for s in self.source.series
            if not query or _fold(query) in _fold(f"{s.code} {s.name}")
        ]
        return pd.DataFrame(rows, columns=["series_id", "name", "freq", "start", "end", "datagroup"])

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        if request.formula or request.agg or request.freq:
            raise InvalidInput(
                f"{self.source.id}: formül, agregasyon ve frekans dönüşümü desteklenmiyor; dönüşümü yerelde yapın"
            )
        vintage = datetime.now(UTC)
        frames = []
        for code in request.series_ids:
            seed = self._seed(code)
            rows = [(parse_period(p), v) for p, v in self._observations(seed, request.period.start, request.period.end)]
            frame = pd.DataFrame(
                {
                    "source_id": self.source.id, "dataset_id": split_remote(seed.remote or "")[0].split(",")[1],
                    "series_id": code, "entity_id": "", "entity_name": None, "metric": metric_name(0),
                    "date": [d for d, _ in rows], "freq": seed.freq.value, "value": [v for _, v in rows],
                    "unit": seed.unit, "currency": seed.currency, "is_proxy": False, "vintage_ts": vintage,
                    "publication_lag_days": self.source.lag_for(code),
                }
            )  # fmt: skip
            frames.append(frame)
        out = pd.concat(frames, ignore_index=True) if frames else empty_observations()
        return out[OBSERVATION_COLUMNS]

    def healthcheck(self) -> HealthResult:
        if not self.source.canaries:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "katalogda canary seri yok")
        today, details, started = date.today(), [], time.monotonic()
        try:
            for seed in self.source.canaries:
                start = today - timedelta(days=_LOOKBACK_DAYS.get(seed.freq.value, 800))
                df = self.fetch(FetchRequest.create(self.source.id, [seed.code], start, today))
                if df.empty:
                    return HealthResult(self.source.id, HealthStatus.FAIL, f"{seed.code}: son dönemde gözlem yok")
                last = df.sort_values("date").iloc[-1]
                age = (today - last["date"]).days
                if age > Frequency(seed.freq).max_staleness_days + _STALENESS_MARGIN_DAYS:
                    return HealthResult(
                        self.source.id, HealthStatus.FAIL, f"{seed.code}: son gözlem {last['date']} ({age} gün önce)"
                    )
                details.append(f"{seed.code} son={last['date']} değer={last['value']:g}")
        except ProviderError as exc:
            return HealthResult(self.source.id, HealthStatus.FAIL, f"{type(exc).__name__}: {exc}")
        return HealthResult(
            self.source.id, HealthStatus.OK, "; ".join(details), (time.monotonic() - started) * 1000,
            tuple(s.code for s in self.source.canaries),
        )  # fmt: skip

    def close(self) -> None:
        self._http.close()
