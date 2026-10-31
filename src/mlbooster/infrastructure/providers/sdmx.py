"""SDMX-CSV sağlayıcısı (BIS, OECD): anahtarsız, resmi istatistik API'leri.

Güvenlik: kullanıcı yalnızca katalogdaki güvenli takma adları (ör. `BIS.REER.TR`) kullanır; gerçek
SDMX yolu (`WS_EER/M.R.B.TR`) kataloğun `remote` alanından gelir ve şema doğrulamasından geçer.
İstekler yalnızca kaynağa göre kodda sabitlenmiş host'a gider (katalog başka host gösterirse sağlayıcı
çalışmaz). Yönlendirme takip edilmez, yanıt boyutu sınırlıdır (SecureHttpClient).
"""

from __future__ import annotations

import calendar
import csv
import io
import re
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlencode, urlsplit

import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

# Kaynak → izinli host ve CSV format parametresi (iki API farklı adlandırır).
HOSTS = {
    "bis": ("stats.bis.org", "csv"),
    "oecd": ("sdmx.oecd.org", "csvfile"),
}
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$")
_HALF = re.compile(r"^(\d{4})-[SH]([12])$")
_YEAR = re.compile(r"^(\d{4})$")
_LOOKBACK = {"D": 30, "B": 30, "W": 60, "M": 400, "Q": 800, "S": 1200, "A": 2200}


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_period(raw: str) -> date:
    raw = raw.strip()
    try:
        if m := _DAY.match(raw):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _MONTH.match(raw):
            return _month_end(int(m[1]), int(m[2]))
        if m := _QUARTER.match(raw):
            return _month_end(int(m[1]), int(m[2]) * 3)
        if m := _HALF.match(raw):
            return _month_end(int(m[1]), int(m[2]) * 6)
        if m := _YEAR.match(raw):
            return date(int(m[1]), 12, 31)
    except ValueError:
        pass
    raise UnexpectedResponse(f"Tanınmayan SDMX dönemi: {raw[:30]!r}")


class SdmxCsvProvider:
    def __init__(
        self,
        source: Source,
        *,
        http: SecureHttpClient,
        secrets: EnvSecretProvider | None = None,
        raw_store: RawStore | None = None,
    ):
        if source.id not in HOSTS:
            raise ProviderError(f"SDMX sağlayıcısı {source.id} kaynağını tanımıyor: {sorted(HOSTS)}")
        host, self._format = HOSTS[source.id]
        parts = urlsplit(source.base_url)
        if parts.scheme != "https" or parts.hostname != host:
            raise ProviderError(f"{source.id} sağlayıcısı yalnızca https://{host} ile çalışır")
        self.source = source
        self._base = source.base_url.rstrip("/") + "/"
        self._http = http
        self._raw_store = raw_store

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is None or not seed.remote:
            known = ", ".join(s.code for s in self.source.series)
            raise InvalidInput(f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}")
        return seed

    def build_url(self, remote: str, start: date, end: date) -> str:
        path = quote(remote, safe="/,@+.-_")
        params = urlencode({"format": self._format, "startPeriod": start.isoformat(), "endPeriod": end.isoformat()})
        return f"{self._base}{path}?{params}"

    def _get_csv(self, url: str) -> list[dict]:
        response: HttpResponse = self._http.get(url, headers={"Accept": "text/csv"})
        if response.status == 404:
            return []  # SDMX: istenen dönemde veri yok
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"{self.source.id} {response.status} yönlendirmesi: {url}")
        if response.status != 200:
            raise ProviderError(f"{self.source.id} HTTP {response.status}: {url}")
        if "html" in response.content_type or response.content.lstrip()[:1] == b"<":
            raise UnexpectedResponse(f"{self.source.id} CSV yerine HTML/XML döndü: {url}")
        if self._raw_store is not None:
            self._raw_store.save(self.source.id, url, response.content, "text/csv")
        text = response.content.decode("utf-8-sig", errors="replace")
        rows = list(csv.DictReader(io.StringIO(text)))
        if rows and not {"TIME_PERIOD", "OBS_VALUE"} <= set(rows[0]):
            raise UnexpectedResponse(f"{self.source.id} yanıtında TIME_PERIOD/OBS_VALUE yok: {list(rows[0])[:12]}")
        return rows

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        rows = [(s.code, s.name, s.freq.value, None, None, s.remote) for s in self.source.series]
        return pd.DataFrame(rows, columns=["series_id", "name", "freq", "start", "end", "datagroup"])

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        if request.formula or request.agg or request.freq:
            raise InvalidInput(f"{self.source.id}: formül/agregasyon/frekans dönüşümü desteklenmez; yerelde dönüştürün")
        vintage = datetime.now(UTC)
        frames = []
        for code in request.series_ids:
            seed = self._seed(code)
            rows = self._get_csv(self.build_url(seed.remote, request.period.start, request.period.end))
            periods = [r["TIME_PERIOD"] for r in rows]
            if len(periods) != len(set(periods)):
                raise UnexpectedResponse(f"{code}: anahtar birden fazla seri döndürdü; katalogdaki remote anahtarı tam belirtilmeli")
            values = pd.to_numeric(pd.Series([r["OBS_VALUE"] for r in rows], dtype="object"), errors="coerce")
            frame = pd.DataFrame(
                {
                    "source_id": self.source.id, "dataset_id": seed.remote.split("/")[0], "series_id": code,
                    "entity_id": "", "entity_name": None, "metric": metric_name(0), "date": [parse_period(p) for p in periods],
                    "freq": seed.freq.value, "value": values, "unit": seed.unit, "currency": seed.currency,
                    "is_proxy": False, "vintage_ts": vintage, "publication_lag_days": self.source.lag_for(code),
                }
            )  # fmt: skip
            frames.append(frame.dropna(subset=["value"]))
        out = pd.concat(frames, ignore_index=True) if frames else empty_observations()
        return out[OBSERVATION_COLUMNS]

    def healthcheck(self) -> HealthResult:
        if not self.source.canaries:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "katalogda canary seri yok")
        today, details, started = date.today(), [], time.monotonic()
        try:
            for seed in self.source.canaries:
                start = today - timedelta(days=_LOOKBACK.get(seed.freq.value, 800))
                df = self.fetch(FetchRequest.create(self.source.id, [seed.code], start, today))
                if df.empty:
                    return HealthResult(self.source.id, HealthStatus.FAIL, f"{seed.code}: son dönemde gözlem yok")
                last = df.sort_values("date").iloc[-1]
                age = (today - last["date"]).days
                if age > Frequency(seed.freq).max_staleness_days + 60:  # uluslararası kaynaklar daha geç yayımlar
                    return HealthResult(self.source.id, HealthStatus.FAIL, f"{seed.code}: son gözlem {last['date']} ({age} gün)")
                details.append(f"{seed.code} son={last['date']} değer={last['value']:g}")
        except ProviderError as exc:
            return HealthResult(self.source.id, HealthStatus.FAIL, f"{type(exc).__name__}: {exc}")
        return HealthResult(
            self.source.id, HealthStatus.OK, "; ".join(details), (time.monotonic() - started) * 1000,
            tuple(s.code for s in self.source.canaries),
        )  # fmt: skip

    def close(self) -> None:
        self._http.close()
