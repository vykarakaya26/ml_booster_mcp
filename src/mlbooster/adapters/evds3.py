"""TCMB EVDS3 adapter'ı.

Uç: https://evds3.tcmb.gov.tr/igmevdsms-dis/  — parametreler path'e '?' OLMADAN eklenir.
API anahtarı yalnızca `key` header'ında gider; URL'ye ve loglara asla yazılmaz.
Canlı gözlem (2026-10-30): anahtarsız çağrı 403 JSON, geçersiz anahtar 401 text/plain,
eski evds2.tcmb.gov.tr/service/evds/ ise 302 ile SPA'ya yönleniyor.
"""

from __future__ import annotations

import calendar
import json
import re
import time
from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd

from mlbooster.adapters.base import (
    AdapterError,
    AuthError,
    BaseAdapter,
    HealthResult,
    MissingCredentials,
    SchemaError,
)
from mlbooster.config import secret
from mlbooster.schema import SILVER_COLUMNS, empty_silver

FREQ_CODES = {"D": 1, "B": 2, "W": 3, "SM": 4, "M": 5, "Q": 6, "S": 7, "A": 8}
FORMULAS = {
    0: "level",
    1: "pct_change",
    2: "diff",
    3: "yoy_pct",
    4: "yoy_diff",
    5: "ytd_pct",
    6: "ytd_diff",
    7: "moving_avg",
    8: "moving_sum",
}
AGGREGATIONS = {"avg", "min", "max", "first", "last", "sum"}
# Parçalama için gözlem başına en kısa takvim süresi (muhafazakâr: chunk ≤ max_obs gözlem).
_MIN_PERIOD_DAYS = {"D": 1, "B": 1, "W": 7, "SM": 14, "M": 28, "Q": 89, "S": 181, "A": 365}
_FREQ_ORDER = ["D", "B", "W", "SM", "M", "Q", "S", "A"]
_FREQ_LABELS = {
    "GÜNLÜK": "D", "DAILY": "D",
    "İŞGÜNÜ": "B", "IŞGÜNÜ": "B", "BUSINESS": "B", "WORKDAY": "B",
    "HAFTALIK": "W", "WEEKLY": "W",
    "AYDA 2 KEZ": "SM", "BIWEEKLY": "SM", "TWO TIMES IN A MONTH": "SM",
    "AYLIK": "M", "MONTHLY": "M",
    "ÜÇ AYLIK": "Q", "3 AYLIK": "Q", "QUARTERLY": "Q",
    "ALTI AYLIK": "S", "6 AYLIK": "S", "SEMIANNUAL": "S", "SEMI-ANNUAL": "S", "SIX MONTH": "S",
    "YILLIK": "A", "ANNUAL": "A", "YEARLY": "A",
}  # fmt: skip

_DMY = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_YM = re.compile(r"^(\d{4})-(\d{1,2})$")
_YQ = re.compile(r"^(\d{4})-Q([1-4])$", re.IGNORECASE)
_YS = re.compile(r"^(\d{4})-[SH]([12])$", re.IGNORECASE)
_Y = re.compile(r"^(\d{4})$")


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_evds_date(raw: str) -> tuple[date, str]:
    """EVDS 'Tarih' alanını dönem sonu tarihine çevirir; (tarih, çıkarılan frekans) döner."""
    raw = raw.strip()
    if m := _DMY.match(raw):
        return date(int(m[3]), int(m[2]), int(m[1])), "D"
    if m := _YM.match(raw):
        return _month_end(int(m[1]), int(m[2])), "M"
    if m := _YQ.match(raw):
        return _month_end(int(m[1]), int(m[2]) * 3), "Q"
    if m := _YS.match(raw):
        return _month_end(int(m[1]), int(m[2]) * 6), "S"
    if m := _Y.match(raw):
        return date(int(m[1]), 12, 31), "A"
    raise SchemaError(f"Tanınmayan EVDS tarih biçimi: {raw!r}")


def normalize_freq_label(label: str | None) -> str | None:
    if not label:
        return None
    return _FREQ_LABELS.get(label.strip().upper(), label.strip())


def date_chunks(start: date, end: date, freq: str, max_obs: int) -> list[tuple[date, date]]:
    span = timedelta(days=max_obs * _MIN_PERIOD_DAYS.get(freq, 1) - 1)
    chunks, cursor = [], start
    while cursor <= end:
        chunk_end = min(cursor + span, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _fmt(d: date) -> str:
    return d.strftime("%d-%m-%Y")


class Evds3Adapter(BaseAdapter):
    source_id = "evds3"

    # --- URL / istek -------------------------------------------------------------------

    def build_url(self, path: str = "", **params: object) -> str:
        """Parametreler path'e '?' olmadan, '&' ile eklenir. Boş parametreler atlanır."""
        base = self.source.base_url.rstrip("/") + "/"
        query = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
        return f"{base}{path}{query}"

    def _headers(self) -> dict[str, str]:
        env_var = self.source.auth.env_var or "EVDS_API_KEY"
        key = secret(env_var)
        if not key:
            raise MissingCredentials(f"{env_var} tanımlı değil (.env veya ortam değişkeni)")
        return {self.source.auth.header_name or "key": key}

    def _get_json(self, url: str) -> object:
        response = self.http.get(url, headers=self._headers())
        self._check_response(url, response)
        self._save_bronze(url, response)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise SchemaError(f"JSON çözülemedi: {url}") from exc

    @staticmethod
    def _check_response(url: str, response: httpx.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            raise AuthError(f"EVDS kimlik doğrulama hatası {status}: {response.text[:200]}")
        if 300 <= status < 400:
            raise SchemaError(
                f"EVDS {status} yönlendirmesi ({response.headers.get('location')}); eski uç mu kullanılıyor? {url}"
            )
        if status != 200:
            raise AdapterError(f"EVDS HTTP {status}: {url} — {response.text[:200]}")
        content_type = response.headers.get("content-type", "").lower()
        if "html" in content_type or response.content.lstrip()[:1] == b"<":
            raise SchemaError(f"EVDS JSON yerine HTML döndü (uç değişmiş olabilir): {url}")
        if not response.content.strip():
            raise SchemaError(f"EVDS boş yanıt döndü: {url}")

    # --- Keşif ---------------------------------------------------------------------------

    def categories(self) -> pd.DataFrame:
        return pd.DataFrame(self._get_json(self.build_url("categories/", type="json")))

    def datagroups(self, category_id: int | None = None) -> pd.DataFrame:
        if category_id is None:
            url = self.build_url("datagroups/", mode=0, type="json")
        else:
            url = self.build_url("datagroups/", mode=2, code=category_id, type="json")
        return pd.DataFrame(self._get_json(url))

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        """query bir veri grubu kodu ise (örn. bie_dkdovizgn) /serieList çağrılır; yoksa katalog tohumları."""
        columns = ["series_id", "name", "freq", "start", "end", "datagroup"]
        if not query:
            rows = [
                {"series_id": s.code, "name": s.name, "freq": s.freq, "start": None, "end": None,
                 "datagroup": s.datagroup}
                for s in self.source.series
            ]  # fmt: skip
            return pd.DataFrame(rows, columns=columns)
        raw = pd.DataFrame(self._get_json(self.build_url("serieList/", type="json", code=query)))
        if raw.empty:
            return pd.DataFrame(columns=columns)
        if "SERIE_CODE" not in raw.columns:
            raise SchemaError(f"serieList yanıtında SERIE_CODE yok; kolonlar: {list(raw.columns)}")
        get = lambda col: raw[col] if col in raw.columns else None  # noqa: E731
        freq_raw = get("FREQUENCY_STR")
        return pd.DataFrame(
            {
                "series_id": raw["SERIE_CODE"],
                "name": get("SERIE_NAME"),
                "freq": freq_raw.map(normalize_freq_label) if freq_raw is not None else None,
                "start": get("START_DATE"),
                "end": get("END_DATE"),
                "datagroup": get("DATAGROUP_CODE") if "DATAGROUP_CODE" in raw.columns else query,
            },
            columns=columns,
        )

    # --- Veri ----------------------------------------------------------------------------

    def fetch(
        self,
        series_ids: list[str],
        start: date,
        end: date,
        freq: str | None = None,
        agg: str | None = None,
        formula: int = 0,
    ) -> pd.DataFrame:
        if not series_ids:
            raise ValueError("En az bir seri kodu gerekli")
        if freq is not None and freq not in FREQ_CODES:
            raise ValueError(f"Geçersiz frekans {freq!r}; seçenekler: {list(FREQ_CODES)}")
        if agg is not None and agg not in AGGREGATIONS:
            raise ValueError(f"Geçersiz agregasyon {agg!r}; seçenekler: {sorted(AGGREGATIONS)}")
        if formula not in FORMULAS:
            raise ValueError(f"Geçersiz formül {formula}; seçenekler: {FORMULAS}")
        if start > end:
            raise ValueError("start > end")

        # Formül uygulanınca orijinal değerler None döner; düzeyi her zaman ayrıca iste.
        formulas = [0] if formula == 0 else [0, formula]
        vintage = datetime.now(UTC)
        frames = [self._fetch_formula(series_ids, start, end, freq, agg, f, vintage) for f in formulas]
        out = pd.concat(frames, ignore_index=True) if frames else empty_silver()
        return out[SILVER_COLUMNS]

    def _chunk_freq(self, series_ids: list[str], freq: str | None) -> str:
        if freq:
            return freq
        known = [s.freq for code in series_ids if (s := self.source.seed(code))]
        if len(known) < len(series_ids):
            return "D"  # bilinmeyen seri: en sık frekansı varsay
        return min(known, key=lambda f: _FREQ_ORDER.index(f) if f in _FREQ_ORDER else 0)

    def _fetch_formula(
        self,
        series_ids: list[str],
        start: date,
        end: date,
        freq: str | None,
        agg: str | None,
        formula: int,
        vintage: datetime,
    ) -> pd.DataFrame:
        n = len(series_ids)
        max_obs = self.source.rate_limit.max_obs_per_call or 1000
        items: list[dict] = []
        for chunk_start, chunk_end in date_chunks(start, end, self._chunk_freq(series_ids, freq), max_obs):
            url = self.build_url(
                series="-".join(series_ids),
                startDate=_fmt(chunk_start),
                endDate=_fmt(chunk_end),
                type="json",
                frequency=FREQ_CODES[freq] if freq else None,
                aggregationTypes="-".join([agg] * n) if agg else None,
                formulas="-".join([str(formula)] * n) if formula else None,
            )
            payload = self._get_json(url)
            if not isinstance(payload, dict) or "items" not in payload:
                raise SchemaError(f"EVDS yanıtında 'items' yok: {url}")
            items.extend(payload["items"] or [])
        return self._items_to_silver(items, series_ids, freq, agg, formula, vintage)

    def _items_to_silver(
        self,
        items: list[dict],
        series_ids: list[str],
        freq: str | None,
        agg: str | None,
        formula: int,
        vintage: datetime,
    ) -> pd.DataFrame:
        if not items:
            return empty_silver()
        raw = pd.DataFrame(items)
        if "Tarih" not in raw.columns:
            raise SchemaError(f"EVDS yanıtında 'Tarih' kolonu yok; kolonlar: {list(raw.columns)}")
        parsed = raw["Tarih"].astype(str).map(parse_evds_date)
        metric = FORMULAS[formula] + (f"_{agg}" if agg else "")
        frames = []
        for code in series_ids:
            column = self._series_column(raw.columns, code, formula)
            if column is None:
                raise SchemaError(f"{code} için kolon bulunamadı; kolonlar: {list(raw.columns)}")
            seed = self.source.seed(code)
            values = pd.to_numeric(raw[column], errors="coerce")
            frame = pd.DataFrame(
                {
                    "source_id": self.source.id,
                    "dataset_id": (seed.datagroup if seed and seed.datagroup else self.source.id),
                    "series_id": code,
                    "entity_id": "",
                    "entity_name": None,
                    "metric": metric,
                    "date": [d for d, _ in parsed],
                    "freq": freq or (seed.freq if seed else None) or parsed.iloc[0][1],
                    "value": values,
                    "unit": seed.unit if seed else None,
                    "currency": seed.currency if seed else None,
                    "is_proxy": False,
                    "vintage_ts": vintage,
                    "publication_lag_days": (
                        seed.publication_lag_days
                        if seed and seed.publication_lag_days is not None
                        else self.source.publication_lag_days
                    ),
                }
            )
            frames.append(frame.dropna(subset=["value"]))
        out = pd.concat(frames, ignore_index=True)
        # Parça sınırlarında tekrar edebilecek gözlemleri temizle.
        return out.drop_duplicates(["series_id", "metric", "freq", "date"], keep="last").reset_index(drop=True)

    @staticmethod
    def _series_column(columns: pd.Index, code: str, formula: int = 0) -> str | None:
        # Formüllü yanıtta kolon 'KOD-<formül>' olabilir; düzey kolonu ise None döner.
        base = code.replace(".", "_")
        candidates = [f"{base}-{formula}", base] if formula else [base]
        if found := next((c for c in candidates if c in columns), None):
            return found
        return next((c for c in columns if c.startswith(base + "-")), None)

    # --- Sağlık ----------------------------------------------------------------------------

    def healthcheck(self) -> HealthResult:
        canaries = [s for s in self.source.series if s.canary]
        if not canaries:
            return HealthResult(self.source.id, "skipped", "katalogda canary seri yok")
        try:
            self._headers()
        except MissingCredentials as exc:
            return HealthResult(self.source.id, "skipped", str(exc))
        lookback = {"D": 30, "B": 30, "W": 60, "SM": 90, "M": 400, "Q": 800, "S": 1200, "A": 2200}
        today = date.today()
        details, started = [], time.monotonic()
        try:
            for seed in canaries:
                start = today - timedelta(days=lookback.get(seed.freq, 400))
                df = self.fetch([seed.code], start, today)
                if df.empty:
                    return HealthResult(self.source.id, "fail", f"{seed.code}: son dönemde gözlem yok")
                last = df.sort_values("date").tail(3)
                details.append(f"{seed.code} son={last['date'].iloc[-1]} değer={last['value'].iloc[-1]:g}")
        except AdapterError as exc:
            return HealthResult(self.source.id, "fail", f"{type(exc).__name__}: {exc}")
        latency = (time.monotonic() - started) * 1000
        return HealthResult(self.source.id, "ok", "; ".join(details), latency, [s.code for s in canaries])
