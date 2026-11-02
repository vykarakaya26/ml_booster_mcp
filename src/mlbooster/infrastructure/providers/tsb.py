"""TSB provider: company-level written premiums by branch from Türkiye Sigorta Birliği's public statistics files.

Live findings 2026-11-02 (CLAUDE.md source rule 5):
- www.tsb.org.tr has no robots.txt (404) and publishes no open data licence; the footer reserves all rights to TSB
  (recorded in the catalog licence note). The files are member-company aggregates, no personal data.
- The statistics page (/tr/istatistik/genel-sigorta-verileri/prim-adet) lists its files through a public JSON endpoint:
  GET `/Statistic/GetStatistics?pageId=1&periodYear=<y>&statisticPeriodId=0&periodMonth=<m>&statisticCategoryId=1&
  statisticSubCategoryId=1` → `{Result: [{Id, FileName, FilePath, PeriodYear, PeriodMonth, CreatedDate}], TotalCount}`;
  one month has about ten files, an unpublished month an empty list. Older entries spell Turkish letters with combining
  marks, so labels are compared as normalised keys. `FilePath` is a static file under
  /content/Statistics/ (spaces and Turkish letters in the name; revised uploads get a new name such as "...(2).xlsx").
- The page can switch file downloads to a form (name, company, e-mail, reCAPTCHA, one-time code); the switch is
  `/Statistic/GetStatisticReportStatus` (`Value: "false"` on 2026-11-02, so the files are plain links). The provider
  reads the switch first and stops if the form is on: the gate is never worked around.
- "3 Prim Üretimleri Sıralama <yyyy-mm>" has one sheet per branch ("Trafik", "Kasko", "Hastalık-Sağlık", ...) plus
  "Hayatdışı", "Hayat" and "Genel Toplam": a rank, company name, TSB company code and "Toplam Üretim (TL)" for the
  period and for the same period a year earlier, "İLK 10/20 ŞİRKET" subtotals and a "SEKTÖR TOPLAMI" row (code 9003).
  Values are cumulative from 1 January (year to date, TL). The layout is unchanged since 2014-10, the first .xlsx file;
  earlier months are legacy .xls files and are skipped. Gaps: 2016-04 is listed but its file answers 404, 2017-10 is
  not listed; the monthly premium of those months and of the month after them is therefore missing.
- Publication: Sep 2026 on 19 Oct, Aug 2026 on 16–21 Sep, Jul 2026 on 17 Aug (about 20 days). The files state that
  the figures are provisional and are revised by later uploads.

The catalog `remote` field is `PRIM_URETIMLERI_SIRALAMA/<SHEET>[.YTD]` with the sheet name as a normalised key
(`TRAFIK`, `HASTALIK_SAGLIK`, `GENEL_TOPLAM`, ...). Without `.YTD` the monthly premium is derived from the cumulative
figures (month m minus month m−1 of the same year; January as published), so a range start needs the previous file
as well; with `.YTD` the cumulative figure is returned as published. The sector total gets an empty `entity_id`, each
company is an entity (entity_id = TSB company code, entity_name = its latest name in the result). Each file is
validated: the companies must add up to the sector total within 0.5%, and the cumulative total must not fall by more
than 5% from the previous month. Raw files go to the bronze store, whose content hash serves as change detection.
Requests go only to the host pinned below.
"""

from __future__ import annotations

import io
import json
import re
import time
import unicodedata
import zipfile
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlencode, urlsplit

import openpyxl
import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

HOST = "www.tsb.org.tr"
LIST_PATH = "/Statistic/GetStatistics"
STATUS_PATH = "/Statistic/GetStatisticReportStatus"
FILE_PREFIX = "/content/Statistics/"
CATEGORY_ID, SUBCATEGORY_ID = 1, 1  # Genel Sigorta Verileri / Prim Adet
FILES = {"PRIM_URETIMLERI_SIRALAMA": "prim_uretimleri_siralama"}  # file key → dataset_id
FIRST_MONTH = date(2014, 10, 1)  # first .xlsx ranking file
TOTAL_KEY = "SEKTOR_TOPLAMI"
SUBTOTAL_PREFIX = "ILK_"
TOTAL_CODE = 9003
SHARE_TOLERANCE = 0.005  # companies must add up to the sector total within 0.5%
MAX_YTD_DROP = 0.05  # the cumulative sector total may shrink by at most 5% from one month to the next (revisions)
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024
_MAX_ROWS = 1000
_HEALTH_LOOKBACK_DAYS = 120
_STALENESS_MARGIN_DAYS = 15
_SAFE_PATH = re.compile(r"^/content/Statistics/[^/\\?#%]{1,200}$")

_REMOTE = re.compile(r"^(?P<file>[A-Z0-9_]{1,40})/(?P<sheet>[A-Z][A-Z0-9_]{1,59})(?P<ytd>\.YTD)?$")
_TR_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "IIiSsGgUuOoCc")
_PERIOD = re.compile(r"^(\d{4})-(\d{1,2})$")


def label_key(label: str) -> str:
    """Stable key of a sheet, file or row label: ASCII upper case, other characters folded to '_'. Older listing
    entries spell Turkish letters with combining marks ("s" + U+0327), so the label is decomposed first."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", label) if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", "_", plain.translate(_TR_FOLD).upper()).strip("_")


def parse_remote(remote: str) -> tuple[str, str, bool]:
    """(file key, sheet key, cumulative?) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    if m is None or m["file"] not in FILES:
        raise InvalidInput(f"TSB remote alanı 'PRIM_URETIMLERI_SIRALAMA/<SAYFA>[.YTD]' biçiminde olmalıdır: {remote!r}")
    return m["file"], m["sheet"], m["ytd"] is not None


def _months(start: date, end: date) -> list[tuple[int, int]]:
    first = max(start, FIRST_MONTH)
    y, m = first.year, first.month
    out = []
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _previous(year: int, month: int) -> tuple[int, int] | None:
    """Previous month of the same year (the cumulative figures restart every January)."""
    return (year, month - 1) if month > 1 else None


def _month_end(year: int, month: int) -> date:
    return date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)


class _Sheet:
    """One branch sheet of one month: sector total and {company code: (name, value)}, cumulative TL."""

    __slots__ = ("companies", "total")

    def __init__(self, total: float, companies: dict[str, tuple[str, float]]):
        self.total = total
        self.companies = companies


class TsbProvider:
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

    # ------------------------------------------------------------------------------------------------ requests

    def _get_json(self, path: str, query: dict[str, object] | None = None) -> dict:
        url = f"https://{HOST}{path}" + (f"?{urlencode(query)}" if query else "")
        response = self._http.get(url, headers={"Accept": "application/json"})
        self._check_status(response)
        if "json" not in response.content_type or response.content.lstrip()[:1] == b"<":
            raise UnexpectedResponse(f"TSB JSON yerine başka bir içerik döndürdü ({response.content_type[:40]}): {url}")
        try:
            doc = json.loads(response.content)
        except ValueError:
            raise UnexpectedResponse(f"TSB yanıtı çözümlenemedi: {url}") from None
        if not isinstance(doc, dict):
            raise UnexpectedResponse(f"TSB yanıtı beklenen nesne biçiminde değil: {url}")
        return doc

    @staticmethod
    def _check_status(response: HttpResponse) -> None:
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"TSB {response.status} yönlendirmesi döndürdü: {response.url}")
        if response.status != 200:
            raise ProviderError(f"TSB HTTP {response.status}: {response.url}")

    def _ensure_open_downloads(self) -> None:
        """Stops if TSB has put the files behind its contact form (e-mail verification); it is never bypassed."""
        doc = self._get_json(STATUS_PATH)
        value = str(doc.get("Value", "")).strip().lower()
        if value == "true":
            raise ProviderError(
                "TSB istatistik dosyalarının indirilmesini form ve e-posta doğrulamasına bağlamış; otomatik indirme "
                "yapılmıyor. Dosyaları tsb.org.tr üzerinden elle indirip tablo olarak yükleyebilirsiniz."
            )
        if value != "false":
            raise UnexpectedResponse("TSB indirme durumu okunamadı (GetStatisticReportStatus)")

    def _file_path(self, file_key: str, year: int, month: int) -> str | None:
        """Path of the month's file from the statistics listing; None if the month is not published (or only as .xls)."""
        doc = self._get_json(
            LIST_PATH,
            {"pageId": 1, "periodYear": year, "statisticPeriodId": 0, "periodMonth": month,
             "statisticCategoryId": CATEGORY_ID, "statisticSubCategoryId": SUBCATEGORY_ID},
        )  # fmt: skip
        result = doc.get("Result")
        if result is None:
            return None
        if not isinstance(result, list) or not all(isinstance(r, dict) for r in result):
            raise UnexpectedResponse(f"TSB dosya listesi beklenen biçimde değil ({year}/{month})")
        matches = [
            r for r in result
            if file_key in label_key(str(r.get("FileName", ""))) and r.get("PeriodYear") == year and r.get("PeriodMonth") == month
        ]  # fmt: skip
        if not matches:
            return None
        latest = max(matches, key=lambda r: int(r.get("Id") or 0))  # a revised upload gets a new, higher Id
        path = str(latest.get("FilePath", ""))
        if not _SAFE_PATH.fullmatch(path) or ".." in path:
            raise UnexpectedResponse(f"TSB dosya yolu beklenen klasörde değil ({year}/{month})")
        if path.lower().endswith(".xls"):
            return None  # legacy format (before 2014-11): not read
        if not path.lower().endswith(".xlsx"):
            raise UnexpectedResponse(f"TSB dosyası beklenen Excel biçiminde değil ({year}/{month})")
        return path

    def _download(self, path: str) -> bytes | None:
        """File content; None if the listed file is missing on the server (e.g. 2016-04 answers 404)."""
        url = f"https://{HOST}{quote(path)}"
        response = self._http.get(url)
        if response.status == 404:
            return None
        self._check_status(response)
        if response.content[:2] != b"PK":
            raise UnexpectedResponse(f"TSB Excel yerine başka bir içerik döndürdü ({response.content_type[:40]}): {url}")
        if self._raw_store is not None:
            self._raw_store.save(self.source.id, url, response.content, "spreadsheet")
        return response.content

    # ------------------------------------------------------------------------------------------------ port

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

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        if request.formula or request.agg or request.freq:
            raise InvalidInput(
                f"{self.source.id}: formül, agregasyon ve frekans dönüşümü desteklenmiyor; dönüşümü yerelde yapın"
            )
        seeds = [(seed, parse_remote(seed.remote or "")) for seed in map(self._seed, request.series_ids)]
        months = _months(request.period.start, request.period.end)
        if not months:
            return empty_observations()
        wanted = set(months)
        if (prev := _previous(*months[0])) and any(not ytd for _, (_, _, ytd) in seeds):
            wanted.add(prev)  # the first monthly figure needs the previous cumulative one
        self._ensure_open_downloads()
        vintage, rows = datetime.now(UTC), []
        for file_key in sorted({f for _, (f, _, _) in seeds}):
            sheets = {s for _, (f, s, _) in seeds if f == file_key}
            ytd: dict[tuple[int, int], dict[str, _Sheet]] = {}
            for year, month in sorted(wanted):
                path = self._file_path(file_key, year, month)
                content = self._download(path) if path is not None else None
                if content is not None:
                    ytd[(year, month)] = _read_workbook(content, sheets, year, month)
            _check_consistency(ytd)
            for seed, (f, sheet, cumulative) in seeds:
                if f == file_key:
                    rows += _rows(seed.code, sheet, cumulative, ytd, months)
        frame = pd.DataFrame(rows, columns=["series_id", "entity_id", "entity_name", "date", "value"])
        if frame.empty:
            return empty_observations()
        latest = frame[frame["entity_id"] != ""].sort_values("date").groupby("entity_id")["entity_name"].last()
        frame["entity_name"] = frame["entity_id"].map(latest).fillna(frame["entity_name"])  # renamed companies
        by_code = {seed.code: seed for seed, _ in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = frame["series_id"].map(lambda c: FILES[parse_remote(by_code[c].remote or "")[0]])
        frame["metric"] = metric_name(0)
        frame["freq"] = frame["series_id"].map(lambda c: by_code[c].freq.value)
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c].unit)
        frame["currency"] = frame["series_id"].map(lambda c: by_code[c].currency)
        frame["is_proxy"] = False  # every insurance company is a TSB member and reports to these statistics
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


# ---------------------------------------------------------------------------------------------------- parsing


def _read_workbook(content: bytes, sheets: set[str], year: int, month: int) -> dict[str, _Sheet]:
    """The requested branch sheets of one monthly ranking file."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_UNZIPPED_BYTES:
                raise UnexpectedResponse(f"TSB Excel dosyası açıldığında çok büyük ({year}/{month})")
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        raise UnexpectedResponse(f"TSB Excel dosyası okunamadı ({year}/{month})") from None
    try:
        titles = {label_key(ws.title): ws for ws in workbook.worksheets}
        missing = sorted(sheets - set(titles))
        if missing:
            raise UnexpectedResponse(f"TSB dosyasında '{missing[0]}' sayfası yok ({year}/{month})")
        return {key: _parse_sheet(titles[key], key, year, month) for key in sorted(sheets)}
    finally:
        workbook.close()


def _parse_sheet(ws, key: str, year: int, month: int) -> _Sheet:
    rows = [tuple(r) for _, r in zip(range(_MAX_ROWS), ws.iter_rows(values_only=True), strict=False)]
    header = next((i for i, r in enumerate(rows[:20]) if any(label_key(str(c or "")) == "SIRKET_KODU" for c in r)), None)
    if header is None:
        raise UnexpectedResponse(f"TSB '{key}' sayfasında başlık satırı bulunamadı ({year}/{month})")
    labels = [label_key(str(c or "")) for c in rows[header]]
    if "SIRKET_ADI" not in labels:
        raise UnexpectedResponse(f"TSB '{key}' sayfasının kolon yapısı değişmiş ({year}/{month})")
    name_col, code_col = labels.index("SIRKET_ADI"), labels.index("SIRKET_KODU")
    # The current period label ("2026-09") sits above its own "Toplam Üretim (TL)" column; the same header repeats for
    # the period a year earlier, so the value column is taken from the label's position.
    periods = [(j, m) for r in rows[:header] for j, c in enumerate(r) if isinstance(c, str) and (m := _PERIOD.fullmatch(c.strip()))]
    if not periods or (int(periods[0][1][1]), int(periods[0][1][2])) != (year, month):
        raise UnexpectedResponse(f"TSB '{key}' sayfasının dönemi istenen ayla uyuşmuyor ({year}/{month})")
    value_col = periods[0][0]
    if labels[value_col] != "TOPLAM_URETIM_TL":
        raise UnexpectedResponse(f"TSB '{key}' sayfasının kolon yapısı değişmiş ({year}/{month})")
    total, companies = None, {}
    for row in rows[header + 1 :]:
        texts = [label_key(c) for c in row[: name_col + 1] if isinstance(c, str)]
        value = row[value_col] if value_col < len(row) else None
        if TOTAL_KEY in texts:
            total = _number(value, key, year, month)
            break
        code = row[code_col] if code_col < len(row) else None
        name = row[name_col] if name_col < len(row) else None
        if any(t.startswith(SUBTOTAL_PREFIX) for t in texts):
            continue  # "İLK 10 ŞİRKET" / "İLK 20 ŞİRKET" (code 0 or empty)
        if isinstance(code, bool) or not isinstance(code, int | float) or not isinstance(name, str) or not name.strip():
            continue  # blank or note row
        if value is None or code <= 0 or code == TOTAL_CODE:
            continue
        entity = str(int(code))
        if entity in companies:
            raise UnexpectedResponse(f"TSB '{key}' sayfasında {entity} şirket kodu tekrarlanıyor ({year}/{month})")
        companies[entity] = (" ".join(name.split()), _number(value, key, year, month))
    if total is None:
        raise UnexpectedResponse(f"TSB '{key}' sayfasında sektör toplamı satırı yok ({year}/{month})")
    listed = sum(v for _, v in companies.values())
    if abs(listed - total) > SHARE_TOLERANCE * abs(total) + 1.0:
        raise UnexpectedResponse(
            f"TSB '{key}' sayfasında şirketlerin toplamı sektör toplamını tutmuyor ({year}/{month}): "
            f"paylar toplamı %{listed / total * 100:.2f}"
        )
    return _Sheet(total, companies)


def _number(value: object, key: str, year: int, month: int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise UnexpectedResponse(f"TSB '{key}' sayfasında sayısal olmayan değer ({year}/{month}): {str(value)[:20]!r}")
    return float(value)


def _check_consistency(ytd: dict[tuple[int, int], dict[str, _Sheet]]) -> None:
    """The cumulative sector total of a branch must not shrink noticeably within a year."""
    for (year, month), sheets in ytd.items():
        prev = ytd.get(_previous(year, month) or (0, 0))
        if prev is None:
            continue
        for key, sheet in sheets.items():
            before = prev[key].total
            if before > 0 and sheet.total < before * (1 - MAX_YTD_DROP):
                raise UnexpectedResponse(
                    f"TSB '{key}' birikimli sektör toplamı {year}/{month} döneminde önceki aydan düşük; dosyalar tutarsız"
                )


def _rows(code: str, sheet: str, cumulative: bool, ytd: dict[tuple[int, int], dict[str, _Sheet]], months) -> list[tuple]:
    rows = []
    for year, month in months:
        current = ytd.get((year, month))
        if current is None:
            continue
        now = current[sheet]
        if cumulative or month == 1:
            total, values = now.total, {e: v for e, (_, v) in now.companies.items()}
        else:
            before = ytd.get((year, month - 1))
            if before is None:
                continue  # the previous cumulative figure is missing: the monthly premium cannot be derived
            prev = before[sheet]
            total = now.total - prev.total
            values = {e: v - prev.companies.get(e, ("", 0.0))[1] for e, (_, v) in now.companies.items()}
            # A company that drops out of the list mid-year (merger, licence transfer) is written with the reversal of
            # its cumulative figure, so that the companies still add up to the monthly sector total.
            values |= {e: -v for e, (_, v) in prev.companies.items() if e not in now.companies and v}
        names = {e: n for e, (n, _) in (ytd.get((year, month - 1), {}).get(sheet, now).companies.items())}
        names |= {e: n for e, (n, _) in now.companies.items()}
        period_end = _month_end(year, month)
        rows.append((code, "", "Sektör toplamı", period_end, total))
        rows += [(code, e, names[e], period_end, v) for e, v in sorted(values.items())]
    return rows
