"""ODMD provider: monthly passenger car and light commercial vehicle sales by brand (keyless, anonymous).

Live findings 2026-11-02 (CLAUDE.md source rule 5):
- www.odmd.org.tr has no robots.txt (404) and no terms-of-use page; the footer reads "© ODMD - Tüm Hakları Saklıdır".
  The monthly reports are published openly to inform members, the press and the public (licence note in the catalog).
- The report list "Pazar - Perakende Satışlar" (`neuralnetwork.aspx?type=36`, further pages `sortial.aspx?linkpos=N`)
  holds one entry per month ("2026 Eylül Perakende Satışlar (Yerli & İthal)") plus year-to-date entries, newest first,
  12 per page. The same brand table is the last-but-one page of the press bulletin PDF (type=35, released the same day).
- An entry is downloaded through `wf_docudownload.aspx?primary_id=<id>&type=36&...&downloadfirst=yes`: with the
  ASP.NET session cookie that the list page sets it answers 302 to `/folders/2837/categorial1docs/<id>/<file>`;
  without the cookie it answers 200 with an empty HTML page. No login, token or browser check is involved.
- Files: xlsx from 2015-01 on (except 2017-02, a legacy xls), xls in 2014 and one-page PDFs in 2011-2013. Only xlsx
  is read (openpyxl): the series start in 2015-01 and months in another format are skipped with a warning. Each
  workbook has one sheet: a title row "PERAKENDE SATIŞLAR YERLİ / İTHAL DAĞILIMI: EYLÜL-2026", a header row
  OTOMOBİL / HAFİF TİCARİ / TOPLAM (the "MARKA" cell is missing in some years), a sub-header row YERLİ / İTHAL /
  TOPLAM, one row per brand (empty cell = 0) and a "TOPLAM:" row (2020-12 has an extra, partly filled "TOPLAM/TOTAL"
  row before it). Tesla figures are ODMD estimates (footnote since 2023-05).
- Renamed brands: SSANGYONG → KG MOBILITY – SSANGYONG (2024-02), JAECOO → OMODA & JAECOO (2026-03); each is kept as
  one entity.
- Bulletins appear 1-4 days after the month ends (e.g. Sep 2026 on 2 Oct 2026).

Every workbook is validated before use: the title must name the requested month, brands must add up to the "TOPLAM:"
row (±0.5 %), passenger car + light commercial must equal the total, and the market total must not jump more than
tenfold against the previous month. Raw files go to the bronze store, whose content hash serves as change detection.

The catalog `remote` field is `perakende/<SEGMENT>.<COLUMN>` (segment OTOMOBIL, HAFIF_TICARI or TOPLAM; column TOPLAM,
YERLI or ITHAL). The market total is written with an empty `entity_id`; brands are entities (entity_id = brand key).
Requests go only to the host pinned below.
"""

from __future__ import annotations

import html
import io
import logging
import re
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

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

log = logging.getLogger(__name__)

HOST = "www.odmd.org.tr"
REPORT_TYPE = 36  # "Pazar - Perakende Satışlar"
LIST_FIRST = f"/web_2837_1/neuralnetwork.aspx?type={REPORT_TYPE}"
LIST_PAGE = (
    "/web_2837_1/sortial.aspx?linkpos={page}&target=categorial1&type=36&primary_id=&detail=single&sp_table="
    "&sp_primary=&sp_fields=&sp_language=&sp_table_extra=&extracriteria=&language_id=1&search_fields=&search_values="
)
DOWNLOAD = (
    "/web_2837_1/wf_docudownload.aspx?primary_id={id}&type=36&target=categorial1&detail=single&sp_table="
    "&sp_primary=&sp_table_extra=&openfrom=sortial&downloadfirst=yes"
)
FIRST_MONTH = date(2015, 1, 1)  # earlier months are xls (2014) or PDF (2011-2013)
MAX_LIST_PAGES = 40
MAX_ROWS = 400
MAX_UNZIPPED_BYTES = 20 * 1024 * 1024
SHARE_TOLERANCE = 0.005  # brands must add up to the total within ±0.5 %
MAX_MONTHLY_JUMP = 10.0  # total market vs previous month (Jan 2019 was 0.18 × Dec 2018)
SEGMENTS = ("OTOMOBIL", "HAFIF_TICARI", "TOPLAM")
COLUMNS = ("YERLI", "ITHAL", "TOPLAM")
SEGMENT_LABELS = {"OTOMOBIL": "otomobil", "HAFIF_TICARI": "hafif ticari", "TOPLAM": "otomobil ve hafif ticari"}
TOTAL_NAME = "Toplam pazar"
_HEALTH_LOOKBACK_DAYS = 120
_STALENESS_MARGIN_DAYS = 15

MONTHS = {
    "OCAK": 1, "SUBAT": 2, "MART": 3, "NISAN": 4, "MAYIS": 5, "HAZIRAN": 6,
    "TEMMUZ": 7, "AGUSTOS": 8, "EYLUL": 9, "EKIM": 10, "KASIM": 11, "ARALIK": 12,
}  # fmt: skip
# Brands that were renamed; older labels map to the current key so that a brand stays one entity over time.
BRAND_ALIASES: dict[str, str] = {"SSANGYONG": "KG_MOBILITY_SSANGYONG", "JAECOO": "OMODA_JAECOO"}
BRAND_NAMES: dict[str, str] = {"KG_MOBILITY_SSANGYONG": "KG MOBILITY – SSANGYONG", "OMODA_JAECOO": "OMODA & JAECOO"}
LEGACY_FORMATS = (b"\xd0\xcf\x11\xe0", b"%PDF")  # xls (OLE) and PDF files of older months

_REMOTE = re.compile(r"^perakende/(?P<segment>OTOMOBIL|HAFIF_TICARI|TOPLAM)\.(?P<column>YERLI|ITHAL|TOPLAM)$")
_LIST_ITEM = re.compile(
    r"class=\"td_reports_name\"\s+onclick=\"return dRep\('\?primary_id=(?P<id>\d{1,9})&(?:amp;)?type=36[^\"]*\"\s*>"
    r"(?P<title>[^<]{1,200})</td>"
)
_LIST_PAGES = re.compile(r"sortial\.aspx\?linkpos=(\d{1,3})&(?:amp;)?target=categorial1&(?:amp;)?type=36")
_MONTHLY_TITLE = re.compile(r"^(?P<year>\d{4}) (?:YILI )?\(?(?P<month>[A-Z]+)\)? PERAKENDE SATISLAR")
_SHEET_TITLE = re.compile(r"DAGILIMI\s*:\s*(?P<month>[A-Z]+)[\s\-]*(?P<year>\d{4})")
_LOCATION = re.compile(r"^/folders/2837/categorial1docs/(?P<id>\d{1,9})/[^/?#\\]{1,200}$")
_SESSION = re.compile(r"ASP\.NET_SessionId=([A-Za-z0-9]{8,64})")


def fold(text: str) -> str:
    """ASCII upper case without Turkish diacritics (also handles decomposed characters such as 'u' + U+0308)."""
    text = unicodedata.normalize("NFKD", str(text).replace("ı", "i"))
    return "".join(c for c in text if not unicodedata.combining(c)).upper()


def brand_key(label: str) -> str:
    """Stable entity id of a brand label: ASCII upper case, without footnote stars, words joined with '_'."""
    key = re.sub(r"[^A-Z0-9]+", "_", fold(label).replace("*", " ")).strip("_")
    return BRAND_ALIASES.get(key, key)


def brand_name(key: str, label: str) -> str:
    """Display name that stays the same for one entity across months ('ASTON MARTİN' in 2016-01 → 'ASTON MARTIN')."""
    return BRAND_NAMES.get(key) or " ".join(fold(label).replace("*", " ").split())


def parse_remote(remote: str) -> tuple[str, str]:
    """(segment, column) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    if m is None:
        raise InvalidInput(f"ODMD remote alanı 'perakende/<SEGMENT>.<KOLON>' biçiminde olmalıdır: {remote!r}")
    return m["segment"], m["column"]


def _month_end(year: int, month: int) -> date:
    return date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)


def _months(start: date, end: date) -> list[tuple[int, int]]:
    first = max(start, FIRST_MONTH)
    y, m, out = first.year, first.month, []
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


@dataclass
class RetailTable:
    """One monthly workbook: values[segment][column][brand key], totals[segment][column], brand labels."""

    year: int
    month: int
    values: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    totals: dict[str, dict[str, float]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def market_total(self) -> float:
        return self.totals["TOPLAM"]["TOPLAM"]


def _number(raw: object, where: str) -> float:
    if raw is None or raw == "":
        return 0.0
    if isinstance(raw, bool):
        raise UnexpectedResponse(f"ODMD tablosunda sayısal olmayan değer ({where})")
    if isinstance(raw, int | float):
        return float(raw)
    text = str(raw).strip().replace(".", "").replace(",", ".")
    try:
        return float(text) if text not in {"", "-"} else 0.0
    except ValueError:
        raise UnexpectedResponse(f"ODMD tablosunda sayısal olmayan değer ({where}): {str(raw)[:20]!r}") from None


def _header(rows: list[tuple]) -> tuple[int, dict[int, tuple[str, str]]]:
    """Index of the sub-header row and {column index: (segment, column)} from the two header rows."""
    for i, row in enumerate(rows[:-1]):
        cells = [fold(c) if c is not None else "" for c in row]
        starts = {j: s for j, c in enumerate(cells) for s in SEGMENTS if c.replace(" ", "_") == s}
        if set(starts.values()) != set(SEGMENTS):
            continue
        sub = [fold(c) if c is not None else "" for c in rows[i + 1]]
        layout, segment = {}, None
        for j, c in enumerate(sub):
            segment = starts.get(j, segment)
            if segment is not None and c in COLUMNS:
                layout[j] = (segment, c)
        if len(layout) != len(SEGMENTS) * len(COLUMNS) or len(set(layout.values())) != len(layout):
            raise UnexpectedResponse("ODMD tablosunun başlık yapısı değişmiş (YERLİ / İTHAL / TOPLAM kolonları)")
        return i + 1, layout
    raise UnexpectedResponse("ODMD tablosunda OTOMOBİL / HAFİF TİCARİ / TOPLAM başlığı bulunamadı")


def parse_workbook(content: bytes) -> RetailTable:
    """Brand table of a monthly 'Perakende Satışlar (Yerli & İthal)' workbook, validated."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_UNZIPPED_BYTES:
                raise UnexpectedResponse("ODMD dosyası açıldığında boyut sınırını aşıyor")
        book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError):
        raise UnexpectedResponse("ODMD dosyası okunamadı (geçerli bir xlsx değil)") from None
    try:
        sheet = book.worksheets[0]
        rows = []
        for row in sheet.iter_rows(values_only=True, max_row=MAX_ROWS):
            if any(v is not None and str(v).strip() for v in row):
                rows.append(tuple(row))
    finally:
        book.close()
    if not rows:
        raise UnexpectedResponse("ODMD dosyasında tablo yok")
    title = _SHEET_TITLE.search(fold(rows[0][0] or ""))
    if title is None or title["month"] not in MONTHS:
        raise UnexpectedResponse(f"ODMD tablo başlığında dönem bulunamadı: {str(rows[0][0])[:80]!r}")
    table = RetailTable(int(title["year"]), MONTHS[title["month"]])
    sub, layout = _header(rows)
    table.values = {s: {c: {} for c in COLUMNS} for s in SEGMENTS}
    grand = next(j for j, sc in layout.items() if sc == ("TOPLAM", "TOPLAM"))
    in_brands = True
    for row in rows[sub + 1 :]:
        label = row[0]
        if label is None or not str(label).strip():
            continue
        where = f"{table.year}-{table.month:02d}, {str(label)[:30]}"
        if fold(label).startswith("TOPLAM"):
            in_brands = False  # the last total row with a market total wins (2020-12 has a partial one first)
            if grand < len(row) and row[grand] is not None:
                table.totals = {s: {} for s in SEGMENTS}
                for j, (s, c) in layout.items():
                    table.totals[s][c] = _number(row[j] if j < len(row) else None, where)
            continue
        if not in_brands:
            continue  # footnotes
        key = brand_key(str(label))
        if not key:
            continue
        if key in table.labels:
            raise UnexpectedResponse(f"ODMD tablosunda tekrarlanan marka: {str(label)[:40]!r}")
        table.labels[key] = brand_name(key, str(label))
        for j, (s, c) in layout.items():
            table.values[s][c][key] = _number(row[j] if j < len(row) else None, where)
    if not table.totals:
        raise UnexpectedResponse(f"ODMD tablosunda TOPLAM satırı yok ({table.year}-{table.month:02d})")
    _validate(table)
    return table


def _validate(table: RetailTable) -> None:
    period = f"{table.year}-{table.month:02d}"
    if not table.labels or table.market_total <= 0:
        raise UnexpectedResponse(f"ODMD tablosunda marka satırı ya da pazar toplamı yok ({period})")
    def close(a: float, b: float) -> bool:
        return abs(a - b) <= max(1.0, SHARE_TOLERANCE * b)

    for s in SEGMENTS:
        for c in COLUMNS:
            total, brands = table.totals[s][c], sum(table.values[s][c].values())
            if not close(brands, total):
                raise UnexpectedResponse(
                    f"ODMD {period}: {SEGMENT_LABELS[s]} ({c}) markalarının toplamı ({brands:,.0f}) TOPLAM satırıyla "
                    f"({total:,.0f}) uyuşmuyor".replace(",", ".")
                )
        if not close(table.totals[s]["YERLI"] + table.totals[s]["ITHAL"], table.totals[s]["TOPLAM"]):
            raise UnexpectedResponse(f"ODMD {period}: {SEGMENT_LABELS[s]} yerli ve ithal toplamı uyuşmuyor")
    cars, lcv = table.totals["OTOMOBIL"]["TOPLAM"], table.totals["HAFIF_TICARI"]["TOPLAM"]
    if not close(cars + lcv, table.market_total):
        raise UnexpectedResponse(f"ODMD {period}: otomobil ve hafif ticari toplamı pazar toplamıyla uyuşmuyor")


class OdmdProvider:
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
        self._session: str | None = None

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is None or not seed.remote:
            known = ", ".join(s.code for s in self.source.series)
            raise InvalidInput(f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}")
        return seed

    def _get(self, path: str) -> HttpResponse:
        headers = {"Cookie": f"ASP.NET_SessionId={self._session}"} if self._session else None
        response = self._http.get(f"https://{HOST}{path}", headers=headers)
        if self._session is None and (m := _SESSION.search(response.headers.get("set-cookie", ""))):
            self._session = m[1]  # anonymous session that the download links require (no login)
        return response

    def _page(self, page: int) -> str:
        response = self._get(LIST_FIRST if page == 1 else LIST_PAGE.format(page=page))
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"ODMD rapor listesi {response.status} yönlendirmesi döndürdü")
        if response.status != 200:
            raise ProviderError(f"ODMD HTTP {response.status}: rapor listesi, sayfa {page}")
        if "html" not in response.content_type:
            raise UnexpectedResponse("ODMD rapor listesi HTML değil")
        return response.content.decode("utf-8", errors="replace")

    def _reports(self, oldest: tuple[int, int]) -> dict[tuple[int, int], int]:
        """{(year, month): report id} of the monthly entries, read page by page back to `oldest`."""
        reports: dict[tuple[int, int], int] = {}
        page, last_page = 1, 1
        while page <= min(last_page, MAX_LIST_PAGES):
            text = self._page(page)
            last_page = max([last_page, *(int(n) for n in _LIST_PAGES.findall(text))])
            items = _LIST_ITEM.findall(text)
            if not items:
                if page == 1:
                    raise UnexpectedResponse("ODMD rapor listesinde kayıt bulunamadı; sayfa yapısı değişmiş olabilir")
                break
            dated = []
            for report_id, title in items:
                m = _MONTHLY_TITLE.match(fold(" ".join(html.unescape(title).split())))
                if m and m["month"] in MONTHS:
                    period = (int(m["year"]), MONTHS[m["month"]])
                    reports.setdefault(period, int(report_id))  # newest entry first
                    dated.append(period)
            if dated and min(dated) <= oldest:
                break
            page += 1
        return reports

    def _download(self, report_id: int, year: int, month: int) -> bytes | None:
        """The month's xlsx, or None if the month is published only in a legacy format (xls or PDF)."""
        response = self._get(DOWNLOAD.format(id=report_id))
        if response.status == 200:
            raise UnexpectedResponse(
                f"ODMD indirme bağlantısı dosyaya yönlendirmedi ({year}-{month:02d}); oturum açılamadı"
            )
        if response.status not in {301, 302, 303, 307}:
            raise ProviderError(f"ODMD HTTP {response.status}: {year}-{month:02d} raporu")
        location = response.headers.get("location", "")
        if location.startswith(f"https://{HOST}/"):
            location = location[len(f"https://{HOST}") :]
        m = _LOCATION.fullmatch(location)
        if m is None or int(m["id"]) != report_id or ".." in location:
            raise UnexpectedResponse(f"ODMD beklenmeyen bir adrese yönlendirdi ({year}-{month:02d})")
        url = f"https://{HOST}{location}"
        file = self._http.get(url)
        if file.status != 200:
            raise ProviderError(f"ODMD HTTP {file.status}: {year}-{month:02d} dosyası")
        if file.content.startswith(LEGACY_FORMATS):
            log.warning("ODMD %s-%02d is published as xls/PDF; month skipped", year, month)
            return None
        if not file.content.startswith(b"PK"):
            raise UnexpectedResponse(f"ODMD {year}-{month:02d} dosyası xlsx değil ({file.content_type[:40]})")
        if self._raw_store is not None:
            self._raw_store.save(self.source.id, url, file.content, "spreadsheet")
        return file.content

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        q = fold(query or "")
        rows = [
            (s.code, s.name, s.freq.value, FIRST_MONTH, None, s.remote)
            for s in self.source.series
            if not q or q in fold(f"{s.code} {s.name}")
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
        reports = self._reports(months[0])
        vintage, rows, previous = datetime.now(UTC), [], None
        for year, month in months:
            report_id = reports.get((year, month))
            if report_id is None:
                continue  # not published yet (or missing from the list)
            content = self._download(report_id, year, month)
            if content is None:
                continue
            table = parse_workbook(content)
            if (table.year, table.month) != (year, month):
                raise UnexpectedResponse(
                    f"ODMD listesindeki {year}-{month:02d} raporu {table.year}-{table.month:02d} dönemini içeriyor"
                )
            if previous is not None and not (1 / MAX_MONTHLY_JUMP <= table.market_total / previous <= MAX_MONTHLY_JUMP):
                raise UnexpectedResponse(f"ODMD {year}-{month:02d}: pazar toplamı önceki aya göre tutarsız")
            previous = table.market_total
            period_end = _month_end(year, month)
            for seed, (segment, column) in seeds:
                rows.append((seed.code, "", TOTAL_NAME, period_end, table.totals[segment][column]))
                rows += [
                    (seed.code, key, table.labels[key], period_end, value)
                    for key, value in table.values[segment][column].items()
                ]
        frame = pd.DataFrame(rows, columns=["series_id", "entity_id", "entity_name", "date", "value"])
        if frame.empty:
            return empty_observations()
        by_code = {seed.code: seed for seed, _ in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = "perakende_satislar"
        frame["metric"] = metric_name(0)
        frame["freq"] = frame["series_id"].map(lambda c: by_code[c].freq.value)
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c].unit)
        frame["currency"] = frame["series_id"].map(lambda c: by_code[c].currency)
        frame["is_proxy"] = False  # ODMD reports the whole passenger car and light commercial market
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
