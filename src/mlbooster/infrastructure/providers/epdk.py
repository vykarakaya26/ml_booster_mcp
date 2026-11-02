"""EPDK provider: monthly fuel and LPG sales by licence holder from the public monthly sector reports (keyless).

Live findings 2026-11-02 (CLAUDE.md source rule 5):
- www.epdk.gov.tr has no robots.txt (the path redirects to the home page) and no terms-of-use page; the reports are
  public publications without login. Requests go only to the host pinned below.
- The report lists (/Detay/Icerik/3-0-104/... for petroleum, /Detay/Icerik/3-0-105/... for LPG) are static HTML:
  one collapsible entry per month ("2026 Yılı Petrol Piyasası Ağustos Ayı Sektör Raporu") followed by its files at
  /Detay/DownloadDocument?id=<id> (application/octet-stream), typed by the icon next to the link (pdf/excel/word).
- Formats: 2016-01..2025-12 Word (.docx) reports, petroleum table 3.2 "Lisans Sahibi Şirketlere Göre Yurtiçi Satış
  Miktarları" and LPG table 3.5 "... LPG Satışlarının Dağıtıcılara ve Ürün Türüne Göre Dağılımı"; from 2026-01 a
  PDF report plus an Excel annex with the same figures (petroleum table 12, LPG table 12 in long format). 2025's
  Excel files hold only province tables, so Word comes first. Reports up to 2015 are binary .doc or PDF and are not
  read; the 2016 LPG tables name distributors by short brand ("AKPET", "GÜVENAL", "OMV POAŞ") that do not match
  their titles from 2017 on, so LPG series start in 2017 (FIRST_MONTH).
- Figures are tonnes, every licence holder of the month is listed and the table ends with a total row. A revised
  report replaces the file behind the list entry (new document id); the bronze store keeps every downloaded file
  under its content hash, which serves as change detection.

Checks before anything is written: holders add up to the published total within ±0.5 % (shares sum to 100±0.5),
published shares add up to 100±0.5, and a series total moves less than threefold from one month to the next. A total
row that contradicts both its rows and its published shares is replaced by the holders' sum (see `validate`).

The catalog `remote` field is `<report>/<COLUMN>`: report `petrol` (columns BENZIN, MOTORIN, FUEL_OIL, HAVACILIK,
DENIZCILIK, GAZYAGI, DIGER, TOPLAM) or `lpg` (TUPLU, DOKME, OTOGAZ, TOPLAM). The total row is written with an empty
`entity_id`; licence holders are entities (entity_id = ASCII key of the first two words of the company title, see
`epdk_reports.holder`, with renamed holders mapped by ALIASES).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlsplit

import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.providers.epdk_reports import (
    LPG_COLUMNS,
    PETROL_COLUMNS,
    Document,
    MonthTable,
    fold,
    lpg_sales,
    parse_listing,
    petrol_sales,
    read_office,
)
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

log = logging.getLogger(__name__)

HOST = "www.epdk.gov.tr"
LISTINGS = {
    "petrol": "/Detay/Icerik/3-0-104/petrolaylik-sektor-raporu",
    "lpg": "/Detay/Icerik/3-0-105/lpgaylik-sektor-raporlari",
}
DOWNLOAD_PATH = "/Detay/DownloadDocument"
COLUMNS = {
    "petrol": frozenset(v for v in PETROL_COLUMNS.values() if v != "PAY"),
    "lpg": frozenset(LPG_COLUMNS.values()),
}
FIRST_MONTH = {"petrol": date(2016, 1, 1), "lpg": date(2017, 1, 1)}
SHARE_TOLERANCE = 0.5  # percentage points
MAX_MONTHLY_RATIO = 3.0
# Renamed licence holders (same company, new title); the switch months were checked for a continuous share.
ALIASES = {
    "OMV_PETROL": ("PETROL_OFISI", "PETROL OFİSİ"),  # "OMV PETROL OFİSİ A.Ş." until 2016
    "TOTAL_OIL": ("GUZEL_ENERJI", "GÜZEL ENERJİ"),  # Total Oil Türkiye A.Ş. -> Güzel Enerji Akaryakıt A.Ş. (2019-10)
    "TUPRAS_TURKIYE": ("TUPRAS", "TÜPRAŞ"),  # "TÜPRAŞ, TÜRKİYE PETROL RAFİNERİLERİ A.Ş." until 2024-11
    "TURKIYE_PETROL": ("TUPRAS", "TÜPRAŞ"),  # "TÜRKİYE PETROL RAFİNERİLERİ A.Ş." from 2024-12
}
_HEALTH_LOOKBACK_DAYS = 150
_STALENESS_MARGIN_DAYS = 15
_KIND_ORDER = {"word": 0, "excel": 1}  # PDF reports are not read
_REMOTE = re.compile(r"^(?P<report>petrol|lpg)/(?P<column>[A-Z_]{3,20})$")


def parse_remote(remote: str) -> tuple[str, str]:
    """(report, column) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    if m is None or m["column"] not in COLUMNS[m["report"]]:
        raise InvalidInput(f"EPDK remote alanı 'petrol/<KOLON>' ya da 'lpg/<KOLON>' biçiminde olmalıdır: {remote!r}")
    return m["report"], m["column"]


def _months(start: date, end: date, report: str) -> list[tuple[int, int]]:
    first = max(start, FIRST_MONTH[report])
    y, m = first.year, first.month
    out = []
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _month_end(year: int, month: int) -> date:
    return date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)


def validate(table: MonthTable, columns: set[str], label: str) -> None:
    """Holders must add up to the published total (shares sum to 100±0.5) for every column that is written, and the
    published shares of a column must add up to 100±0.5.

    A total row that disagrees with its own table while the published shares agree with the holders' sum is a typo
    in the total row (LPG 2023-10: otogaz 308.287 t printed, holders and shares 290.2 thousand t); the holders' sum
    then replaces it and a warning is logged."""
    for column in sorted(columns):
        total = table.totals.get(column)
        if total is None:
            raise UnexpectedResponse(f"EPDK {label}: '{column}' kolonu için toplam yok")
        pairs = table.shares.get(column, [])
        if pairs and abs(sum(s for _, s in pairs) - 100) > SHARE_TOLERANCE:
            raise UnexpectedResponse(f"EPDK {label}: '{column}' kolonunda yayımlanan payların toplamı %{sum(s for _, s in pairs):.2f}")
        parts = sum(values.get(column, 0.0) for _, values in table.entities.values())
        if total <= 0:
            if abs(parts) > 1e-6:
                raise UnexpectedResponse(f"EPDK {label}: '{column}' toplamı sıfır, şirketlerin toplamı {parts:g}")
            continue
        if abs(parts / total * 100 - 100) <= SHARE_TOLERANCE:
            continue
        if pairs and parts > 0 and _shares_fit(pairs, parts):
            log.warning("EPDK %s: total row of %s (%.2f) disagrees with its table; using the holders' sum %.2f", label, column, total, parts)
            table.totals[column] = parts
            continue
        raise UnexpectedResponse(
            f"EPDK {label}: '{column}' kolonunda şirketlerin payları toplamı %{parts / total * 100:.2f} (100±0,5 bekleniyor)"
        )


def _shares_fit(pairs: list[tuple[float, float]], parts: float) -> bool:
    """Published shares were computed on the holders' sum (checked on the largest holders, 2-decimal rounding)."""
    largest = sorted(pairs, reverse=True)[:3]
    return all(abs(value / parts * 100 - share) <= 0.05 for value, share in largest)


class EpdkProvider:
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
        self._http = http
        self._raw_store = raw_store

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is None or not seed.remote:
            known = ", ".join(s.code for s in self.source.series)
            raise InvalidInput(f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}")
        return seed

    def _get(self, url: str) -> HttpResponse:
        response = self._http.get(url)
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"EPDK {response.status} yönlendirmesi döndürdü: {url}")
        if response.status != 200:
            raise ProviderError(f"EPDK HTTP {response.status}: {url}")
        return response

    def _listing(self, report: str) -> dict[tuple[int, int], list[Document]]:
        url = f"https://{HOST}{LISTINGS[report]}"
        response = self._get(url)
        if "html" not in response.content_type:
            raise UnexpectedResponse(f"EPDK rapor listesi HTML değil ({response.content_type[:40]}): {url}")
        listing = parse_listing(response.content.decode("utf-8", errors="replace"))
        if not listing:
            raise UnexpectedResponse(f"EPDK rapor listesinde aylık rapor bulunamadı (sayfa yapısı değişmiş olabilir): {url}")
        return listing

    def _month(self, report: str, docs: list[Document], year: int, month: int) -> MonthTable:
        """Sales table of one month: the report's files are tried in order (Word, then Excel) until one holds it."""
        for doc in sorted((d for d in docs if d.kind in _KIND_ORDER), key=lambda d: _KIND_ORDER[d.kind]):
            url = f"https://{HOST}{DOWNLOAD_PATH}?id={quote(doc.doc_id, safe='')}"
            response = self._get(url)
            content = response.content
            if content.lstrip()[:1] == b"<":
                raise UnexpectedResponse(f"EPDK dosya yerine HTML döndürdü: {url}")
            grids = read_office(content)
            if grids is None:  # binary .doc/.xls or a PDF behind a Word/Excel icon
                continue
            table = petrol_sales(grids) if report == "petrol" else lpg_sales(grids, year, month)
            if table is not None:
                if self._raw_store is not None:  # served as application/octet-stream
                    self._raw_store.save(self.source.id, url, content, response.content_type or "application/octet-stream")
                return table
        raise UnexpectedResponse(f"EPDK {report} raporunda {year}/{month:02d} için şirket bazında satış tablosu bulunamadı")

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        needle = fold(query)
        rows = [
            (s.code, s.name, s.freq.value, None, None, s.remote)
            for s in self.source.series
            if not needle or needle in fold(f"{s.code} {s.name}")
        ]
        return pd.DataFrame(rows, columns=["series_id", "name", "freq", "start", "end", "datagroup"])

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        if request.formula or request.agg or request.freq:
            raise InvalidInput(
                f"{self.source.id}: formül, agregasyon ve frekans dönüşümü desteklenmiyor; dönüşümü yerelde yapın"
            )
        seeds = [(seed, parse_remote(seed.remote or "")) for seed in map(self._seed, request.series_ids)]
        vintage, rows = datetime.now(UTC), []
        for report in sorted({r for _, (r, _) in seeds}):
            wanted = [(seed, column) for seed, (r, column) in seeds if r == report]
            months = _months(request.period.start, request.period.end, report)
            listing = self._listing(report) if months else {}
            for year, month in months:
                if (year, month) not in listing:  # not published yet (or never)
                    continue
                table = self._month(report, listing[(year, month)], year, month)
                validate(table, {c for _, c in wanted} | {"TOPLAM"}, f"{report} {year}/{month:02d}")
                rows += _rows(table, wanted, _month_end(year, month))
        frame = pd.DataFrame(rows, columns=["series_id", "entity_id", "entity_name", "date", "value"])
        if frame.empty:
            return empty_observations()
        _check_continuity(frame)
        by_code = {seed.code: (seed, report) for seed, (report, _) in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = frame["series_id"].map(lambda c: f"{by_code[c][1]}_aylik_sektor_raporu")
        frame["metric"] = metric_name(0)
        frame["freq"] = frame["series_id"].map(lambda c: by_code[c][0].freq.value)
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c][0].unit)
        frame["currency"] = frame["series_id"].map(lambda c: by_code[c][0].currency)
        frame["is_proxy"] = False  # every licence holder is listed
        frame["vintage_ts"] = vintage
        frame["publication_lag_days"] = frame["series_id"].map(self.source.lag_for)
        return frame.sort_values(["series_id", "entity_id", "date"], ignore_index=True)[OBSERVATION_COLUMNS]

    def healthcheck(self) -> HealthResult:
        if not self.source.canaries:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "katalogda canary seri yok")
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


def _rows(table: MonthTable, wanted: list[tuple[SeedSeries, str]], period_end: date) -> list[tuple]:
    entities: dict[str, tuple[str, dict[str, float]]] = {}
    for key, (name, values) in table.entities.items():
        key, name = ALIASES.get(key, (key, name))
        merged = entities.setdefault(key, (name, {}))[1]
        for column, value in values.items():
            merged[column] = merged.get(column, 0.0) + value
    rows = []
    for seed, column in wanted:
        rows.append((seed.code, "", "Toplam", period_end, float(table.totals[column])))
        rows += [
            (seed.code, key, name, period_end, float(values[column]))
            for key, (name, values) in entities.items()
            if values.get(column, 0.0) > 0  # holders without sales of this product are left out
        ]
    return rows


def _check_continuity(frame: pd.DataFrame) -> None:
    """A series total that jumps more than threefold between consecutive months points to a misread table."""
    totals = frame[frame["entity_id"] == ""].sort_values("date")
    for code, part in totals.groupby("series_id"):
        dates, values = list(part["date"]), list(part["value"])
        for (d0, v0), (d1, v1) in zip(zip(dates, values, strict=True), zip(dates[1:], values[1:], strict=True), strict=False):
            consecutive = (d1.year * 12 + d1.month) - (d0.year * 12 + d0.month) == 1
            if consecutive and min(v0, v1) > 0 and max(v0, v1) / min(v0, v1) > MAX_MONTHLY_RATIO:
                raise UnexpectedResponse(
                    f"EPDK {code}: toplam {d0:%Y-%m} ile {d1:%Y-%m} arasında {max(v0, v1) / min(v0, v1):.1f} kat değişti; tablo yanlış okunmuş olabilir"
                )
