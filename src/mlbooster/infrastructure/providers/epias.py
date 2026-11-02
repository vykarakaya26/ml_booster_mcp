"""EPİAŞ Şeffaflık Platformu provider: hourly electricity market data, aggregated to daily values.

Public documentation checked 2026-11-02 (seffaflik.epias.com.tr/electricity-service/technical/en/index.html and its
swagger.json; CAS REST guide on epias.com.tr), responses with a valid TGT verified live 2026-11-03:
- Every data service is `POST https://seffaflik.epias.com.tr/electricity-service/v1/...` with a JSON body
  `{"startDate": "2023-01-01T00:00:00+03:00", "endDate": ...}` (ISO-8601, Turkish local time) and a `TGT` header.
  The response is `{"items": [{"date": "...+03:00", "hour": "00:00", <fields>}], "page": null, "statistic(s)"/"totals"}`;
  `date` is the hour in local time. Both dates are days: start..end returns every hour of both days (no paging seen
  even for 8784 rows).
- The offset must be the one valid on that day in Europe/Istanbul (+02:00 in winters before September 2016):
  `2015-01-01T00:00:00+03:00` is 31 Dec 23:00 local time and returns 31 December.
- Range limit per call (400 `(BUS)SEF1117 "... tanımlanmış aralıktan (3 MONTH) fazla olamaz!"`): one year for PTF,
  SMF and consumption, three months for generation; the end may be at most start + N calendar months (Jan 1–Apr 1
  and Nov 30–Feb 28 pass, Jan 1–Apr 2 does not). Requests are therefore cut at calendar month ends.
- Generation `total` = sum of the source fields + `importExport` (net exchange, negative when exporting); domestic
  generation is the sum of the sources (pseudo field `generation`, also the total of `by_source`).
- Data start (live): SMF 2011-01, PTF 2011-11, generation 2013-10, consumption 2016-01 at the latest.
- The TGT comes from `POST https://giris.epias.com.tr/cas/v1/tickets` (form-urlencoded `username`, `password`,
  `Accept: text/plain`); success is `201 Created` with the body `TGT-...`, wrong credentials give 401. The documented
  TGT lifetime is 2 hours (CLAUDE.md says 8; the catalog `ttl_hours` follows the documentation).
- Without a TGT the services answer 401 JSON `{"errors": [{"errorCode": "AUTH002", ...}]}`; an invalid TGT gives
  401 `AUTH009`; the generation service answers 406 text/plain ("... TGT göndermeniz gerekmektedir ...").
- The gateway sends `RateLimit-Limit` per service (observed: 60 per 60 s for the MCP service, 3600 per hour for the
  others); the catalog rps keeps every service under that. robots.txt allows everything.

The catalog `remote` field is `<dataset>/<field>`: the dataset names one of the services below and the field one
of its numeric item fields. For `realtime-generation/by_source` domestic generation is written with an empty
`entity_id` and every generation source is an entity (entity_id = API field). Hourly values are aggregated to daily
ones by the seed `kind` (level → mean, flow → sum); a day missing more than one of its local hours (e.g. today) is
dropped (DST days have 23 or 25 hours).

Credentials (EPIAS_USERNAME, EPIAS_PASSWORD) are read from the environment only, are sent only in the form body to
the pinned login host, and the TGT is sent only in the header to the pinned data host. Neither is written to URLs,
logs, the raw store or error messages. TGTs are cached in memory only.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import (
    AuthenticationFailed,
    CredentialsMissing,
    ProviderError,
    ProviderUnavailable,
    UnexpectedResponse,
)
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, SeriesKind, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.secret_store import EnvSecretProvider, Secret, redact
from mlbooster.infrastructure.storage.raw_store import RawStore

DATA_HOST = "seffaflik.epias.com.tr"
AUTH_HOST = "giris.epias.com.tr"
TICKET_URL = f"https://{AUTH_HOST}/cas/v1/tickets"
USERNAME_ENV = "EPIAS_USERNAME"
PASSWORD_ENV = "EPIAS_PASSWORD"
TGT_HEADER = "TGT"
DEFAULT_TTL_HOURS = 2.0
TTL_MARGIN_SECONDS = 600  # renew a little before the server lets the ticket expire
DEFAULT_BLOCKED_SECONDS = 30
ISTANBUL = ZoneInfo("Europe/Istanbul")
MAX_ITEMS_PER_RESPONSE = 200_000
BY_SOURCE = "by_source"
GENERATION = "generation"  # pseudo field: sum of the generation sources (= total - importExport)
_HEALTH_LOOKBACK_DAYS = 14
_STALENESS_MARGIN_DAYS = 3

_SERVICE = "/electricity-service/v1/"
# dataset key → (service path, numeric item fields, calendar months per call; SEF1117 above that, verified live)
DATASETS: dict[str, tuple[str, frozenset[str], int]] = {
    "dam-mcp": ("markets/dam/data/mcp", frozenset({"price", "priceUsd", "priceEur"}), 12),
    "bpm-smp": ("markets/bpm/data/system-marginal-price", frozenset({"systemMarginalPrice"}), 12),
    "realtime-consumption": ("consumption/data/realtime-consumption", frozenset({"consumption"}), 12),
    "realtime-generation": (
        "generation/data/realtime-generation",
        frozenset({
            "total", "naturalGas", "dammedHydro", "lignite", "river", "importCoal", "wind", "sun", "fueloil",
            "geothermal", "asphaltiteCoal", "blackCoal", "biomass", "naphta", "lng", "importExport", "wasteheat",
        }),
        3,
    ),
}  # fmt: skip
# Entity labels of the generation sources (unknown new fields, e.g. nuclear, keep their API name as label).
GENERATION_SOURCES = {
    "naturalGas": "Doğal gaz", "dammedHydro": "Barajlı hidroelektrik", "lignite": "Linyit", "river": "Akarsu",
    "importCoal": "İthal kömür", "wind": "Rüzgâr", "sun": "Güneş", "fueloil": "Fuel oil", "geothermal": "Jeotermal",
    "asphaltiteCoal": "Asfaltit", "blackCoal": "Taş kömürü", "biomass": "Biyokütle", "naphta": "Nafta", "lng": "LNG",
    "wasteheat": "Atık ısı", "nuclear": "Nükleer", "gasOil": "Motorin",
}  # fmt: skip
_NOT_A_SOURCE = {"date", "hour", "time", "total", "importExport"}  # cross-border exchange is not generation

_REMOTE = re.compile(r"^(?P<dataset>[a-z][a-z-]{1,40})/(?P<field>[A-Za-z_]{1,40})$")
_TGT = re.compile(r"^TGT-[A-Za-z0-9._-]{8,512}$")
_TR_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "IIiSsGgUuOoCc")


def parse_remote(remote: str) -> tuple[str, str]:
    """(dataset, field) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    if m is None or m["dataset"] not in DATASETS:
        raise InvalidInput(
            f"EPİAŞ remote alanı '<veri seti>/<alan>' biçiminde olmalıdır; veri setleri: {sorted(DATASETS)}"
        )
    dataset, field = m["dataset"], m["field"]
    if field not in DATASETS[dataset][1] and not (
        field in (BY_SOURCE, GENERATION) and dataset == "realtime-generation"
    ):
        raise InvalidInput(f"EPİAŞ {dataset} veri setinde '{field}' alanı yok")
    return dataset, field


@dataclass
class _Ticket:
    value: Secret
    expires: float


class TicketCache:
    """In-memory TGT cache shared by provider instances (the registry may open a provider per call).
    Keyed by a hash of the credentials, so a changed password never reuses an old ticket. Never persisted."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Secret | None:
        with self._lock:
            ticket = self._tickets.get(key)
            if ticket is None or ticket.expires <= self._clock():
                self._tickets.pop(key, None)
                return None
            return ticket.value

    def put(self, key: str, value: Secret, ttl_seconds: float) -> None:
        with self._lock:
            self._tickets[key] = _Ticket(value, self._clock() + ttl_seconds)

    def drop(self, key: str) -> None:
        with self._lock:
            self._tickets.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._tickets.clear()


TICKETS = TicketCache()


def _iso(d: date) -> str:
    """Local midnight with the offset valid on that day (Turkey used +02:00 in winter until 2016)."""
    return datetime(d.year, d.month, d.day, tzinfo=ISTANBUL).isoformat()


def month_chunks(start: date, end: date, months: int) -> list[tuple[date, date]]:
    """[start, end] cut at calendar month ends so that each piece spans at most `months` calendar months."""
    chunks, cursor = [], start
    while cursor <= end:
        years, month = divmod(cursor.month - 1 + months, 12)
        chunk_end = min(date(cursor.year + years, month + 1, 1) - timedelta(days=1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _hours_in_day(d: date) -> int:
    """23 or 25 on DST transition days (until 2016), 24 otherwise. Same-zone subtraction ignores DST, hence UTC."""
    nxt = d + timedelta(days=1)
    midnight = [datetime(x.year, x.month, x.day, tzinfo=ISTANBUL).astimezone(UTC) for x in (d, nxt)]
    return round((midnight[1] - midnight[0]).total_seconds() / 3600)


class EpiasProvider:
    def __init__(
        self,
        source: Source,
        *,
        http: SecureHttpClient,
        secrets: EnvSecretProvider,
        raw_store: RawStore | None = None,
        tickets: TicketCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        parts = urlsplit(source.base_url)
        if parts.scheme != "https" or parts.hostname != DATA_HOST:
            raise ProviderError(f"{source.id} provider'ı yalnızca https://{DATA_HOST} ile çalışır")
        if source.auth.ticket_url not in (None, TICKET_URL):
            raise ProviderError(f"{source.id} provider'ı TGT'yi yalnızca {TICKET_URL} adresinden alır")
        self.source = source
        self._http = http
        self._secrets = secrets
        self._raw_store = raw_store
        self._tickets = tickets or TICKETS
        self._sleep = sleep
        ttl_hours = source.auth.ttl_hours or DEFAULT_TTL_HOURS
        self._ttl_seconds = max(60.0, ttl_hours * 3600 - TTL_MARGIN_SECONDS)
        self._blocked_seconds = source.rate_limit.blocked_seconds_on_429 or DEFAULT_BLOCKED_SECONDS

    # --- Authentication ------------------------------------------------------------------

    def _credentials(self) -> tuple[Secret, Secret]:
        username, password = self._secrets.get(USERNAME_ENV), self._secrets.get(PASSWORD_ENV)
        if username is None or password is None:
            raise CredentialsMissing(
                f"{USERNAME_ENV} ve {PASSWORD_ENV} tanımlı değil (.env dosyasında ya da ortam değişkeni olarak tanımlayın)"
            )
        return username, password

    @staticmethod
    def _cache_key(username: Secret, password: Secret) -> str:
        return hashlib.sha256(f"{username.reveal()}\0{password.reveal()}".encode()).hexdigest()

    def _ticket(self) -> tuple[str, Secret]:
        username, password = self._credentials()
        key = self._cache_key(username, password)
        if (cached := self._tickets.get(key)) is not None:
            return key, cached
        response = self._http.post_form(
            TICKET_URL,
            [("username", username.reveal()), ("password", password.reveal())],
            headers={"Accept": "text/plain"},
        )
        if response.status == 401:
            raise AuthenticationFailed(
                "EPİAŞ kullanıcı adı ya da şifresi kabul edilmedi (giris.epias.com.tr, HTTP 401)"
            )
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"EPİAŞ giriş servisi {response.status} yönlendirmesi döndürdü")
        if response.status not in (200, 201):
            raise ProviderError(f"EPİAŞ TGT alınamadı: HTTP {response.status}")
        text = response.content.decode("utf-8", errors="replace").strip()
        if not _TGT.fullmatch(text):
            raise UnexpectedResponse("EPİAŞ giriş servisi beklenen TGT biçiminde bir yanıt döndürmedi")
        ticket = Secret(text)
        self._tickets.put(key, ticket, self._ttl_seconds)
        return key, ticket

    # --- Requests ------------------------------------------------------------------------

    def _post(self, dataset: str, start: date, end: date) -> list[dict]:
        url = f"https://{DATA_HOST}{_SERVICE}{DATASETS[dataset][0]}"
        body = {"startDate": _iso(start), "endDate": _iso(end)}
        for attempt in range(2):  # a second try only with a fresh ticket
            key, ticket = self._ticket()
            response = self._send(url, body, ticket)
            if not _auth_rejected(response):
                break
            self._tickets.drop(key)
            if attempt:
                raise AuthenticationFailed(f"EPİAŞ TGT'yi kabul etmedi: {self._error_text(response, ticket)}")
        doc = self._json(response, ticket)
        items = doc.get("items")
        if items is None and isinstance(doc.get("body"), dict):  # error envelope shape, in case data uses it too
            items = doc["body"].get("items")
        if items is None:
            items = []
        if not isinstance(items, list) or len(items) > MAX_ITEMS_PER_RESPONSE:
            raise UnexpectedResponse(f"EPİAŞ yanıtında 'items' listesi yok ya da çok büyük: {url}")
        page = doc.get("page")
        if isinstance(page, dict) and isinstance(page.get("total"), int) and page["total"] > len(items):
            raise UnexpectedResponse(
                f"EPİAŞ yanıtı sayfalanmış ({len(items)}/{page['total']}); veriler eksik kalacağı için istek durduruldu"
            )
        if self._raw_store is not None:  # only the public date range goes into the manifest URL
            self._raw_store.save(self.source.id, f"{url}?{urlencode(body)}", response.content, "application/json")
        return items

    def _send(self, url: str, body: dict, ticket: Secret) -> HttpResponse:
        headers = {TGT_HEADER: ticket.reveal(), "Accept": "application/json"}
        try:
            return self._http.post_json(url, body, headers=headers)
        except ProviderUnavailable as exc:
            if "HTTP 429" not in str(exc):
                raise
        # Still throttled after the client's own retries: wait the documented block time once more.
        self._sleep(self._blocked_seconds)
        return self._http.post_json(url, body, headers=headers)

    def _json(self, response: HttpResponse, ticket: Secret) -> dict:
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"EPİAŞ {response.status} yönlendirmesi döndürdü: {response.url}")
        if response.status != 200:
            raise ProviderError(f"EPİAŞ HTTP {response.status}: {self._error_text(response, ticket)}")
        if "json" not in response.content_type or response.content.lstrip()[:1] == b"<":
            raise UnexpectedResponse(
                f"EPİAŞ JSON yerine başka bir içerik döndürdü ({response.content_type[:40]}): {response.url}"
            )
        try:
            doc = json.loads(response.content)
        except ValueError:
            raise UnexpectedResponse(f"EPİAŞ yanıtı çözümlenemedi: {response.url}") from None
        if not isinstance(doc, dict):
            raise UnexpectedResponse(f"EPİAŞ yanıtı beklenen nesne biçiminde değil: {response.url}")
        return doc

    def _error_text(self, response: HttpResponse, ticket: Secret) -> str:
        """errorCode/errorMessage of an EPİAŞ error body (or the start of a text body), with secrets masked."""
        text = response.text(300)
        try:
            doc = json.loads(response.content)
            errors = doc.get("errors") if isinstance(doc, dict) else None
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                text = f"{errors[0].get('errorCode', '')} {errors[0].get('errorMessage', '')}".strip()
        except ValueError:
            pass
        return redact(text, [self._secrets.get(USERNAME_ENV), self._secrets.get(PASSWORD_ENV), ticket])[:200]

    # --- Catalog -------------------------------------------------------------------------

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is None or not seed.remote:
            known = ", ".join(s.code for s in self.source.series)
            raise InvalidInput(f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}")
        return seed

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        fold = (query or "").translate(_TR_FOLD).casefold()
        rows = [
            (s.code, s.name, s.freq.value, None, None, s.remote)
            for s in self.source.series
            if not fold or fold in f"{s.code} {s.name}".translate(_TR_FOLD).casefold()
        ]
        return pd.DataFrame(rows, columns=["series_id", "name", "freq", "start", "end", "datagroup"])

    # --- Data ----------------------------------------------------------------------------

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        if request.formula or request.agg or request.freq not in (None, Frequency.DAILY):
            raise InvalidInput(
                f"{self.source.id}: saatlik veriler günlük değerlere çevrilir; formül, agregasyon ve başka frekans "
                "desteklenmiyor, dönüşümü yerelde yapın"
            )
        seeds = [(seed, parse_remote(seed.remote or "")) for seed in map(self._seed, request.series_ids)]
        self._credentials()  # fail before any request when the account is not configured
        vintage, frames = datetime.now(UTC), []
        for dataset in sorted({d for _, (d, _) in seeds}):
            items: list[dict] = []
            for start, end in month_chunks(request.period.start, request.period.end, DATASETS[dataset][2]):
                items += self._post(dataset, start, end)
            hourly = _hourly(items, request.period.start, request.period.end)
            for seed, (d, field) in seeds:
                if d == dataset:
                    frames.append(_daily(hourly, seed, field))
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if frame.empty:
            return empty_observations()
        by_code = {seed.code: (seed, remote) for seed, remote in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = frame["series_id"].map(lambda c: by_code[c][1][0])
        frame["metric"] = metric_name(0)
        frame["freq"] = Frequency.DAILY.value
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c][0].unit)
        frame["currency"] = frame["series_id"].map(lambda c: by_code[c][0].currency)
        frame["is_proxy"] = False
        frame["vintage_ts"] = vintage
        frame["publication_lag_days"] = frame["series_id"].map(self.source.lag_for)
        return frame.sort_values(["series_id", "entity_id", "date"], ignore_index=True)[OBSERVATION_COLUMNS]

    def healthcheck(self) -> HealthResult:
        if not self.source.canaries:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "katalogda canary seri yok")
        if self._secrets.get(USERNAME_ENV) is None or self._secrets.get(PASSWORD_ENV) is None:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, f"{USERNAME_ENV} / {PASSWORD_ENV} tanımlı değil")
        today, details, started = date.today(), [], time.monotonic()
        try:
            for seed in self.source.canaries:
                start = today - timedelta(days=_HEALTH_LOOKBACK_DAYS)
                df = self.fetch(FetchRequest.create(self.source.id, [seed.code], start, today))
                total = df[df["entity_id"] == ""]
                if total.empty:
                    return HealthResult(self.source.id, HealthStatus.FAIL, f"{seed.code}: son dönemde gözlem yok")
                last = total.sort_values("date").iloc[-1]
                age = (today - last["date"]).days
                if age > Frequency.DAILY.max_staleness_days + _STALENESS_MARGIN_DAYS:
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


def _auth_rejected(response: HttpResponse) -> bool:
    """401 from the data services, or the generation service's 406 text asking for a TGT."""
    return response.status == 401 or (response.status == 406 and b"TGT" in response.content[:2000])


def _hourly(items: list[dict], start: date, end: date) -> pd.DataFrame:
    """Items as a frame with a local `day` column, limited to [start, end]; repeated hours keep the last value."""
    if not items:
        return pd.DataFrame(columns=["day", "stamp"])
    if not all(isinstance(i, dict) and isinstance(i.get("date"), str) for i in items):
        raise UnexpectedResponse("EPİAŞ yanıtındaki kayıtlarda 'date' alanı yok")
    try:
        stamps = [datetime.fromisoformat(i["date"]) for i in items]
    except ValueError:
        raise UnexpectedResponse("EPİAŞ yanıtında tanınmayan tarih biçimi") from None
    frame = pd.DataFrame(items)
    hours = frame["hour"].astype(str) if "hour" in frame.columns else pd.Series("", index=frame.index)
    # `date` carries the hour already (local time); `hour` is kept in the key for services that put only the day there.
    frame["stamp"] = [f"{s.isoformat()}|{h}" for s, h in zip(stamps, hours, strict=True)]
    frame["day"] = [s.date() for s in stamps]
    frame = frame[(frame["day"] >= start) & (frame["day"] <= end)]
    return frame.drop_duplicates("stamp", keep="last")


def _numeric(hourly: pd.DataFrame, name: str, code: str) -> pd.Series | None:
    """Numeric values of one item field; None if the field holds something else (e.g. text)."""
    if name not in hourly.columns:
        raise UnexpectedResponse(f"{code}: EPİAŞ yanıtında '{name}' alanı yok")
    raw = hourly[name]
    if raw.map(lambda v: v is not None and (isinstance(v, bool) or not isinstance(v, int | float))).any():
        return None
    return pd.to_numeric(raw, errors="coerce")


def _generation_parts(hourly: pd.DataFrame, code: str) -> dict[str, pd.Series]:
    """Hourly values of every generation source (unknown new numeric fields included, text fields skipped)."""
    names = sorted(c for c in hourly.columns if c not in _NOT_A_SOURCE | {"day", "stamp"})
    parts = {name: values for name in names if (values := _numeric(hourly, name, code)) is not None}
    if not parts:
        raise UnexpectedResponse(f"{code}: EPİAŞ üretim yanıtında kaynak alanı yok")
    return parts


def _daily(hourly: pd.DataFrame, seed: SeedSeries, field: str) -> pd.DataFrame:
    """Daily rows (series_id, entity_id, entity_name, date, value) of one seed."""
    columns = ["series_id", "entity_id", "entity_name", "date", "value"]
    if hourly.empty:
        return pd.DataFrame(columns=columns)
    series: dict[str, pd.Series] = {}  # entity_id → hourly values; "" is the series total
    if field in (BY_SOURCE, GENERATION):
        parts = _generation_parts(hourly, seed.code)
        series[""] = pd.concat(parts, axis=1).sum(axis=1, min_count=1)
        if field == BY_SOURCE:
            series.update(parts)
    else:
        values = _numeric(hourly, field, seed.code)
        if values is None:
            raise UnexpectedResponse(f"{seed.code}: EPİAŞ yanıtında sayısal olmayan '{field}' değeri")
        series[""] = values
    how = "sum" if seed.kind is SeriesKind.FLOW else "mean"
    out = []
    for entity, values in series.items():
        grouped = values.groupby(hourly["day"]).agg([how, "count"])
        # A day missing more than one local hour (e.g. today) is dropped; DST days have 23 or 25 hours.
        complete = [n >= _hours_in_day(day) - 1 for day, n in grouped["count"].items()]
        label = GENERATION_SOURCES.get(entity, entity) if entity else None
        out += [(seed.code, entity, label, day, float(v)) for day, v in grouped.loc[complete, how].items()]
    return pd.DataFrame(out, columns=columns)
