"""TCMB EVDS3 provider.

Endpoint: https://evds3.tcmb.gov.tr/igmevdsms-dis/ — parameters are appended to the path WITHOUT '?'.
The API key is sent only in the `key` header; it is never written to the URL, logs or the raw data manifest.
Live observation (2026-10-30): a call without a key returns 403 JSON, an invalid key 401 text/plain,
and the old evds2.tcmb.gov.tr/service/evds/ redirects to the SPA with a 302.

Security: the key is sent only to the host pinned in code (ALLOWED_HOST); if the base_url in the
catalog points to another host, the provider refuses to run. This way a tampered catalog cannot
leak the key to another server.
"""

from __future__ import annotations

import calendar
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import (
    AuthenticationFailed,
    CredentialsMissing,
    ProviderError,
    UnexpectedResponse,
)
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.domain.services.chunking import date_chunks
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.secret_store import EnvSecretProvider, Secret, redact
from mlbooster.infrastructure.storage.raw_store import RawStore

ALLOWED_HOST = "evds3.tcmb.gov.tr"
API_KEY_ENV = "EVDS_API_KEY"
API_KEY_HEADER = "key"
DEFAULT_MAX_OBS = 1000
MAX_ITEMS_PER_RESPONSE = 200_000

FREQ_CODES = {
    Frequency.DAILY: 1, Frequency.BUSINESS: 2, Frequency.WEEKLY: 3, Frequency.SEMI_MONTHLY: 4,
    Frequency.MONTHLY: 5, Frequency.QUARTERLY: 6, Frequency.SEMI_ANNUAL: 7, Frequency.ANNUAL: 8,
}  # fmt: skip
_FREQ_LABELS = {  # keys without spaces (EVDS labels, kept as data)
    "GÜNLÜK": "D", "DAILY": "D",
    "İŞGÜNÜ": "B", "IŞGÜNÜ": "B", "BUSINESS": "B", "WORKDAY": "B",  # live value: "İŞ GÜNÜ"
    "HAFTALIK": "W", "WEEKLY": "W",
    "AYDA2KEZ": "SM", "BIWEEKLY": "SM", "TWOTIMESINAMONTH": "SM",
    "AYLIK": "M", "MONTHLY": "M",
    "ÜÇAYLIK": "Q", "3AYLIK": "Q", "QUARTERLY": "Q",
    "ALTIAYLIK": "S", "6AYLIK": "S", "SEMIANNUAL": "S", "SEMI-ANNUAL": "S", "SIXMONTH": "S",
    "YILLIK": "A", "ANNUAL": "A", "YEARLY": "A",
}  # fmt: skip
_HEALTH_LOOKBACK_DAYS = {"D": 30, "B": 30, "W": 60, "SM": 90, "M": 400, "Q": 800, "S": 1200, "A": 2200}  # wider than the staleness threshold

_DMY = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_YM = re.compile(r"^(\d{4})-(\d{1,2})$")
_YQ = re.compile(r"^(\d{4})-Q([1-4])$", re.IGNORECASE)
_YS = re.compile(r"^(\d{4})-[SH]([12])$", re.IGNORECASE)
_Y = re.compile(r"^(\d{4})$")


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_evds_date(raw: str) -> tuple[date, Frequency]:
    """Converts the EVDS 'Tarih' field to a period-end date; returns (date, inferred frequency)."""
    raw = str(raw).strip()
    try:
        if m := _DMY.match(raw):
            return date(int(m[3]), int(m[2]), int(m[1])), Frequency.DAILY
        if m := _YM.match(raw):
            return _month_end(int(m[1]), int(m[2])), Frequency.MONTHLY
        if m := _YQ.match(raw):
            return _month_end(int(m[1]), int(m[2]) * 3), Frequency.QUARTERLY
        if m := _YS.match(raw):
            return _month_end(int(m[1]), int(m[2]) * 6), Frequency.SEMI_ANNUAL
        if m := _Y.match(raw):
            return date(int(m[1]), 12, 31), Frequency.ANNUAL
    except ValueError:
        pass  # e.g. month=13
    raise UnexpectedResponse(f"Tanınmayan EVDS tarih biçimi: {raw[:40]!r}")


def infer_dmy_frequency(dates: list[date]) -> Frequency:
    """The dd-mm-yyyy format is shared by daily, business-day, weekly and semi-monthly data; distinguish by the gaps."""
    unique = sorted(set(dates))
    if len(unique) < 3:
        return Frequency.DAILY
    gaps = sorted((b - a).days for a, b in zip(unique, unique[1:]))
    median = gaps[len(gaps) // 2]
    if median >= 12:
        return Frequency.SEMI_MONTHLY
    if median >= 6:
        return Frequency.WEEKLY
    return Frequency.DAILY if any(d.weekday() >= 5 for d in unique) else Frequency.BUSINESS


def normalize_freq_label(label: object) -> str | None:
    if not isinstance(label, str) or not label.strip():
        return None
    key = "".join(label.split()).upper()  # variants with spaces such as "İŞ GÜNÜ" / "ÜÇ AYLIK"
    if key in _FREQ_LABELS:
        return _FREQ_LABELS[key]
    if key.startswith(("HAFTALIK", "WEEKLY")):  # "HAFTALIK(CUMA)", "HAFTALIK(ÇARŞAMBA)"
        return "W"
    if key in {"AYDAİKİKEZ", "AYDAIKIKEZ"}:
        return "SM"
    return label.strip()


_TR_FOLD = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c", "â": "a", "î": "i", "û": "u"})


def _fold(text: str) -> str:
    """Normalizes text for Turkish case- and diacritic-insensitive comparison (İ→i, I→ı→i)."""
    return text.replace("İ", "i").replace("I", "ı").lower().translate(_TR_FOLD)


def _fmt(d: date) -> str:
    return d.strftime("%d-%m-%Y")


class Evds3Provider:
    def __init__(
        self,
        source: Source,
        *,
        http: SecureHttpClient,
        secrets: EnvSecretProvider,
        raw_store: RawStore | None = None,
    ):
        parts = urlsplit(source.base_url)
        if parts.scheme != "https" or parts.hostname != ALLOWED_HOST:
            raise ProviderError(f"EVDS3 provider'ı yalnızca https://{ALLOWED_HOST} ile çalışır")
        self.source = source
        self._base = source.base_url.rstrip("/") + "/"
        self._http = http
        self._secrets = secrets
        self._raw_store = raw_store

    # --- URL / request -------------------------------------------------------------------

    def build_url(self, path: str = "", **params: object) -> str:
        """Parameters are appended to the path without '?', joined by '&'. Empty parameters are skipped.
        Values must have passed the domain.validation allowlists before this is called."""
        query = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
        return f"{self._base}{path}{query}"

    def _api_key(self) -> Secret:
        key = self._secrets.get(API_KEY_ENV)
        if key is None:
            raise CredentialsMissing(f"{API_KEY_ENV} tanımlı değil (.env dosyasında ya da ortam değişkeni olarak tanımlayın)")
        return key

    def _get_json(self, url: str) -> object:
        key = self._api_key()
        response = self._http.get(url, headers={API_KEY_HEADER: key.reveal()})
        self._check(response, key)
        if self._raw_store is not None:
            self._raw_store.save(self.source.id, url, response.content, response.content_type)
        try:
            return json.loads(response.content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise UnexpectedResponse(f"EVDS yanıtı JSON olarak çözülemedi: {url}") from None

    @staticmethod
    def _check(response: HttpResponse, key: Secret) -> None:
        status, url = response.status, response.url
        if status in (401, 403):
            raise AuthenticationFailed(f"EVDS kimlik doğrulama hatası {status}: {redact(response.text(), [key])}")
        if 300 <= status < 400:
            raise UnexpectedResponse(f"EVDS {status} yönlendirmesi döndürdü; eski bir endpoint kullanılıyor olabilir: {url}")
        if status != 200:
            raise ProviderError(f"EVDS HTTP {status}: {url}")
        if "html" in response.content_type or response.content.lstrip()[:1] == b"<":
            raise UnexpectedResponse(f"EVDS, JSON yerine HTML döndürdü (endpoint değişmiş olabilir): {url}")
        if not response.content.strip():
            raise UnexpectedResponse(f"EVDS boş yanıt döndürdü: {url}")

    # --- Discovery -----------------------------------------------------------------------

    def categories(self) -> pd.DataFrame:
        return pd.DataFrame(self._get_json(self.build_url("categories/", type="json")))

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        """If query is a data group code (e.g. bie_dkdovizgn), /serieList; otherwise the catalog seeds."""
        columns = ["series_id", "name", "freq", "start", "end", "datagroup"]
        if not query:
            rows = [(s.code, s.name, s.freq.value, None, None, s.datagroup) for s in self.source.series]
            return pd.DataFrame(rows, columns=columns)
        payload = self._get_json(self.build_url("serieList/", type="json", code=query))
        if not isinstance(payload, list):
            raise UnexpectedResponse("serieList yanıtı bir liste değil")
        raw = pd.DataFrame(payload)
        if raw.empty:
            return pd.DataFrame(columns=columns)
        if "SERIE_CODE" not in raw.columns:
            raise UnexpectedResponse(f"serieList yanıtında SERIE_CODE yok; kolonlar: {list(raw.columns)[:20]}")
        col = lambda name: raw[name] if name in raw.columns else None  # noqa: E731
        return pd.DataFrame(
            {
                "series_id": raw["SERIE_CODE"],
                "name": col("SERIE_NAME"),
                "freq": raw["FREQUENCY_STR"].map(normalize_freq_label) if "FREQUENCY_STR" in raw.columns else None,
                "start": col("START_DATE"),
                "end": col("END_DATE"),
                "datagroup": col("DATAGROUP_CODE") if "DATAGROUP_CODE" in raw.columns else query,
            },
            columns=columns,
        )

    def search(self, text: str, limit: int = 20) -> pd.DataFrame:
        """Searches data groups by keyword in their Turkish/English names.
        Every word stem (first ≥4 letters) must match; archived groups are sorted last."""
        columns = ["datagroup", "name", "name_eng", "freq", "start", "end", "archived", "score"]
        payload = self._get_json(self.build_url("datagroups/", mode=0, type="json"))
        if not isinstance(payload, list):
            raise UnexpectedResponse("datagroups yanıtı bir liste değil")
        tokens = [_fold(tok) for tok in text.split() if tok.strip()]
        stems = [tok[: max(4, len(tok) - 2)] for tok in tokens]
        rows = []
        for g in payload:
            if not isinstance(g, dict) or not g.get("DATAGROUP_CODE"):
                continue
            haystack = _fold(f"{g.get('DATAGROUP_NAME', '')} {g.get('DATAGROUP_NAME_ENG', '')} {g['DATAGROUP_CODE']}")
            score = sum(stem in haystack for stem in stems)
            if score == 0:
                continue
            name = str(g.get("DATAGROUP_NAME", ""))
            rows.append(
                {
                    "datagroup": g["DATAGROUP_CODE"],
                    "name": name,
                    "name_eng": g.get("DATAGROUP_NAME_ENG"),
                    "freq": normalize_freq_label(g.get("FREQUENCY_STR")),
                    "start": g.get("START_DATE"),
                    "end": g.get("END_DATE"),
                    "archived": "arşiv" in name.lower() or "archive" in str(g.get("DATAGROUP_NAME_ENG", "")).lower(),
                    "score": score / len(stems),
                }
            )
        df = pd.DataFrame(rows, columns=columns)
        if df.empty:
            return df
        df["_end"] = pd.to_datetime(df["end"], format="%d-%m-%Y", errors="coerce")
        df = df.sort_values(["score", "archived", "_end"], ascending=[False, True, False])
        return df.drop(columns="_end").head(limit).reset_index(drop=True)

    # --- Data ----------------------------------------------------------------------------

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        if request.freq is not None and request.freq not in FREQ_CODES:
            raise InvalidInput(f"EVDS {request.freq.value} frekansını desteklemiyor; seçenekler: {[f.value for f in FREQ_CODES]}")
        # When a formula is applied the original values come back as None; always request the level separately.
        formulas = [0] if request.formula == 0 else [0, request.formula]
        vintage = datetime.now(UTC)
        frames = [self._fetch_formula(request, f, vintage) for f in formulas]
        return pd.concat(frames, ignore_index=True)[OBSERVATION_COLUMNS]

    def _chunk_freq(self, request: FetchRequest) -> Frequency:
        if request.freq:
            return request.freq
        known = [s.freq for code in request.series_ids if (s := self.source.seed(code))]
        if len(known) < len(request.series_ids):
            return Frequency.DAILY  # unknown series: assume the highest frequency
        return min(known, key=lambda f: f.rank)

    def _fetch_formula(self, request: FetchRequest, formula: int, vintage: datetime) -> pd.DataFrame:
        codes, n = request.series_ids, len(request.series_ids)
        max_obs = self.source.rate_limit.max_obs_per_call or DEFAULT_MAX_OBS
        items: list[dict] = []
        for start, end in date_chunks(request.period.start, request.period.end, self._chunk_freq(request), max_obs):
            url = self.build_url(
                series="-".join(codes),
                startDate=_fmt(start),
                endDate=_fmt(end),
                type="json",
                frequency=FREQ_CODES[request.freq] if request.freq else None,
                aggregationTypes="-".join([request.agg.value] * n) if request.agg else None,
                formulas="-".join([str(formula)] * n) if formula else None,
            )
            payload = self._get_json(url)
            batch = payload.get("items", ...) if isinstance(payload, dict) else ...
            if batch is None:
                batch = []
            if not isinstance(batch, list):
                raise UnexpectedResponse(f"EVDS yanıtında 'items' listesi yok: {url}")
            items.extend(batch)
            if len(items) > MAX_ITEMS_PER_RESPONSE:
                raise UnexpectedResponse("EVDS yanıtı beklenenden çok büyük")
        return self._to_observations(items, request, formula, vintage)

    def _to_observations(self, items: list[dict], request: FetchRequest, formula: int, vintage: datetime) -> pd.DataFrame:
        if not items:
            return empty_observations()
        raw = pd.DataFrame(items)
        if "Tarih" not in raw.columns:
            raise UnexpectedResponse(f"EVDS yanıtında 'Tarih' kolonu yok; kolonlar: {list(raw.columns)[:20]}")
        parsed = raw["Tarih"].map(parse_evds_date)
        dates = [d for d, _ in parsed]
        inferred = parsed.iloc[0][1]
        metric = metric_name(formula, request.agg)
        frames = []
        for code in request.series_ids:
            column = self._series_column(raw.columns, code, formula)
            if column is None:
                raise UnexpectedResponse(f"{code} için kolon bulunamadı; kolonlar: {list(raw.columns)[:20]}")
            seed = self.source.seed(code)
            freq = request.freq or (seed.freq if seed else inferred)
            if freq is Frequency.DAILY and not request.freq and not seed:
                observed = [d for d, v in zip(dates, pd.to_numeric(raw[column], errors="coerce")) if pd.notna(v)]
                freq = infer_dmy_frequency(observed)
            frame = pd.DataFrame(
                {
                    "source_id": self.source.id,
                    "dataset_id": seed.datagroup if seed and seed.datagroup else self.source.id,
                    "series_id": code,
                    "entity_id": "",
                    "entity_name": None,
                    "metric": metric,
                    "date": dates,
                    "freq": freq.value,
                    "value": pd.to_numeric(raw[column], errors="coerce"),
                    "unit": seed.unit if seed else None,
                    "currency": seed.currency if seed else None,
                    "is_proxy": False,
                    "vintage_ts": vintage,
                    "publication_lag_days": self.source.lag_for(code),
                }
            )
            frames.append(frame.dropna(subset=["value"]))
        out = pd.concat(frames, ignore_index=True)
        # Remove observations that may repeat at chunk boundaries.
        return out.drop_duplicates(["series_id", "metric", "freq", "date"], keep="last").reset_index(drop=True)

    @staticmethod
    def _series_column(columns: pd.Index, code: str, formula: int = 0) -> str | None:
        # In a formula response the column may be 'KOD-<formula>'; the level column comes back as None.
        base = code.replace(".", "_")
        candidates = [f"{base}-{formula}", base] if formula else [base]
        if found := next((c for c in candidates if c in columns), None):
            return found
        return next((c for c in columns if isinstance(c, str) and c.startswith(base + "-")), None)

    # --- Health -------------------------------------------------------------------------

    def healthcheck(self) -> HealthResult:
        if not self.source.canaries:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "katalogda canary seri yok")
        if self._secrets.get(API_KEY_ENV) is None:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, f"{API_KEY_ENV} tanımlı değil")
        today, details, started = date.today(), [], time.monotonic()
        try:
            for seed in self.source.canaries:
                start = today - timedelta(days=_HEALTH_LOOKBACK_DAYS.get(seed.freq.value, 400))
                df = self.fetch(FetchRequest.create(self.source.id, [seed.code], start, today))
                if df.empty:
                    return HealthResult(self.source.id, HealthStatus.FAIL, f"{seed.code}: son dönemde gözlem yok")
                last = df.sort_values("date").iloc[-1]
                age = (today - last["date"]).days
                if age > seed.freq.max_staleness_days:
                    # The series returns data but has stopped: the code may have been archived (e.g. TP.FG.J0, 2026-01).
                    return HealthResult(
                        self.source.id, HealthStatus.FAIL, f"{seed.code}: son gözlem {last['date']} ({age} gün önce); serinin yayımı durdurulmuş olabilir"
                    )
                details.append(f"{seed.code} son={last['date']} değer={last['value']:g}")
        except ProviderError as exc:
            return HealthResult(self.source.id, HealthStatus.FAIL, f"{type(exc).__name__}: {exc}")
        latency = (time.monotonic() - started) * 1000
        return HealthResult(
            self.source.id, HealthStatus.OK, "; ".join(details), latency, tuple(s.code for s in self.source.canaries)
        )

    def close(self) -> None:
        self._http.close()

