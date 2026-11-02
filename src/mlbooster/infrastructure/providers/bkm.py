"""BKM provider: monthly card payment statistics from the public "Dönemsel Bilgiler" reports (keyless, anonymous).

Live findings 2026-11-02 (CLAUDE.md source rule 4):
- bkm.com.tr/robots.txt allows everything except /wp-admin/. The reports page states "Raporlar BKM kaynak
  gösterilmeksizin yayınlanamaz" (attribution required); the site-wide legal notice (/bkm/yasal-uyari/) reserves
  copying, redistribution, storage and any commercial use to BKM's written permission. Both are recorded in the
  catalog licence note (`commercial: false`, `redistribution: false`).
- Each report is a server-rendered HTML page with one `<table>` per year ("2026 YILI" ... "2017 YILI"); a single GET
  returns the whole history since January 2017. Rows are Turkish month names plus quarter ("1. DÖNEM") and year
  total rows, which are skipped. Header cells use colspan/rowspan (up to three header rows); a column is addressed by
  the keys of its header cells from top to bottom, e.g. `ISLEM_TUTARI_MILYON_TL.TOPLAM`.
- Numbers use Turkish formatting ("1.119.979.451", "2.450.608,36"). Amounts are in million TL, counts in units.
- The "Excel" download (`?xls=1`) is the same HTML table served as application/vnd.ms-excel, so the HTML is parsed
  directly (no spreadsheet library needed). Raw pages go to the bronze store, whose content hash serves as change
  detection.
- The sector report (/secilen-sektore-gore-aylik-gelisim/) takes the merchant group as `?filter_group=<n>`; its
  figures are shopping transactions of domestic and foreign cards in Türkiye (their sum over all groups equals the
  "Yerli ve Yabancı Kartların Yurt İçi Kullanımı / Alışveriş" total of the general report).
- BKM publishes no bank-level figures, so every series is an aggregate (empty `entity_id`).

The catalog `remote` field is `<report slug>[,<filter_group>]/<COLUMN>[+<COLUMN>...]`; several columns are summed,
e.g. `secilen-sektore-gore-aylik-gelisim,12/ISLEM_TUTARI_MILYON_TL.ISLEM_TUTARI_BANKA_KARTI+...`. Requests go only
to the host pinned below.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

HOST = "bkm.com.tr"
FIRST_MONTH = date(2017, 1, 1)  # the reports start with "2017 YILI"
_HEALTH_LOOKBACK_DAYS = 150
_STALENESS_MARGIN_DAYS = 15

_KEY = r"[A-Z0-9_]{1,60}(?:\.[A-Z0-9_]{1,60}){0,3}"
_REMOTE = re.compile(rf"^(?P<page>[a-z0-9]+(?:-[a-z0-9]+){{0,12}})(?:,(?P<group>[0-9]{{1,3}}))?/(?P<columns>{_KEY}(?:\+{_KEY}){{0,5}})$")  # fmt: skip
_TR_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "IIiSsGgUuOoCc")
_YEAR_TITLE = re.compile(r"^(\d{4}) YILI$")
_NUMBER = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$")
_MONTHS = {
    name: i + 1
    for i, name in enumerate(
        ["OCAK", "SUBAT", "MART", "NISAN", "MAYIS", "HAZIRAN", "TEMMUZ", "AGUSTOS", "EYLUL", "EKIM", "KASIM", "ARALIK"]
    )
}


def label_key(label: str) -> str:
    """Stable key of a header label: ASCII upper case, every run of other characters becomes '_'."""
    return re.sub(r"[^A-Z0-9]+", "_", label.translate(_TR_FOLD).upper()).strip("_")


def parse_remote(remote: str) -> tuple[str, str | None, tuple[str, ...]]:
    """(report slug, merchant group or None, columns to sum) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    if m is None:
        raise InvalidInput(f"BKM remote alanı '<rapor>[,<grup>]/<KOLON>[+<KOLON>...]' biçiminde olmalıdır: {remote!r}")
    columns = tuple(m["columns"].split("+"))
    if len(set(columns)) != len(columns):
        raise InvalidInput(f"BKM remote alanında tekrarlanan kolon var: {remote!r}")
    return m["page"], m["group"], columns


def _month_end(year: int, month: int) -> date:
    return date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)


class _Tables(HTMLParser):
    """Cells of every `<table>` as rows of (text, colspan, rowspan); `<br>` becomes a space."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, int, int]]]] = []
        self._stack: list[list[list[tuple[str, int, int]]]] = []
        self._cell: list[str] | None = None
        self._span = (1, 1)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._stack.append([])
        elif not self._stack:
            return
        elif tag == "tr":
            self._stack[-1].append([])
        elif tag in ("td", "th"):
            a = dict(attrs)
            self._cell, self._span = [], (_span(a.get("colspan")), _span(a.get("rowspan")))
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._stack:
            if not self._stack[-1]:
                self._stack[-1].append([])
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._stack[-1][-1].append((text, *self._span))
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _span(value: str | None) -> int:
    return int(value) if value and value.isdigit() and 0 < int(value) <= 50 else 1


def _grid(rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """Expands colspan/rowspan so that every row has one text per column."""
    out: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}  # column -> (text, rows still covered)
    for row in rows:
        line: list[str] = []
        cells = iter(row)
        col = 0
        while True:
            if col in pending:
                text, left = pending.pop(col)
                line.append(text)
                if left > 1:
                    pending[col] = (text, left - 1)
                col += 1
                continue
            cell = next(cells, None)
            if cell is None:
                break
            text, colspan, rowspan = cell
            for _ in range(colspan):
                line.append(text)
                if rowspan > 1:
                    pending[col] = (text, rowspan - 1)
                col += 1
        while col in pending:  # spans reaching past the last cell of this row
            text, left = pending.pop(col)
            line.append(text)
            if left > 1:
                pending[col] = (text, left - 1)
            col += 1
        out.append(line)
    return out


def parse_report(html: str) -> dict[str, dict[date, float]]:
    """{column key: {period end: value}} of a BKM yearly-table report."""
    parser = _Tables()
    parser.feed(html)
    parser.close()
    series: dict[str, dict[date, float]] = {}
    years = 0
    for rows in parser.tables:
        title = _YEAR_TITLE.fullmatch(rows[0][0][0]) if rows and len(rows[0]) == 1 else None
        if title is None:
            continue
        years += 1
        year, grid = int(title[1]), _grid(rows[1:])
        first = next((i for i, line in enumerate(grid) if line and label_key(line[0]) in _MONTHS), None)
        if first is None or first == 0:
            raise UnexpectedResponse(f"BKM raporunda {year} yılı tablosunun başlığı ya da ay satırları yok")
        header = grid[:first]
        width = len(header[-1])
        if any(len(line) != width for line in header):
            raise UnexpectedResponse(f"BKM raporunun {year} yılı başlık yapısı çözümlenemedi")
        keys = [".".join(dict.fromkeys(label_key(line[c]) for line in header if line[c])) for c in range(width)]
        for line in grid[first:]:
            month = _MONTHS.get(label_key(line[0])) if line else None
            if month is None:  # quarter and year totals, empty separator rows
                continue
            if len(line) != width:
                raise UnexpectedResponse(f"BKM raporunda {year}/{month} satırının kolon sayısı başlıkla uyuşmuyor")
            for key, text in zip(keys[1:], line[1:], strict=True):
                if text in ("", "-"):
                    continue
                if not _NUMBER.fullmatch(text):
                    raise UnexpectedResponse(f"BKM raporunda sayısal olmayan değer: {text[:20]!r}")
                series.setdefault(key, {})[_month_end(year, month)] = float(text.replace(".", "").replace(",", "."))
    if years == 0:
        raise UnexpectedResponse("BKM sayfasında yıllık istatistik tablosu bulunamadı")
    return series


class BkmProvider:
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

    def _page(self, page: str, group: str | None) -> dict[str, dict[date, float]]:
        url = f"https://{HOST}/{page}/" + (f"?filter_group={group}" if group is not None else "")
        response = self._http.get(url, headers={"Accept": "text/html"})
        html = self._html(response)
        if self._raw_store is not None:
            self._raw_store.save(self.source.id, url, response.content, "text/html")
        return parse_report(html)

    def _html(self, response: HttpResponse) -> str:
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"BKM {response.status} yönlendirmesi döndürdü: {response.url}")
        if response.status != 200:
            raise ProviderError(f"BKM HTTP {response.status}: {response.url}")
        if "html" not in response.content_type:
            raise UnexpectedResponse(f"BKM HTML yerine başka bir içerik döndürdü ({response.content_type[:40]}): {response.url}")
        return response.content.decode("utf-8", errors="replace")

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
        start, end = max(request.period.start, FIRST_MONTH), request.period.end
        rows: list[tuple[str, date, float]] = []
        if start <= end:
            pages = {(page, group) for _, (page, group, _) in seeds}
            parsed = {key: self._page(*key) for key in sorted(pages, key=lambda k: (k[0], k[1] or ""))}
            for seed, (page, group, columns) in seeds:
                report = parsed[(page, group)]
                missing = [c for c in columns if c not in report]
                if missing:
                    raise UnexpectedResponse(f"{seed.code}: BKM raporunda '{missing[0]}' kolonu bulunamadı ({page})")
                common = set.intersection(*(set(report[c]) for c in columns))  # a sum needs every part
                rows += [
                    (seed.code, d, sum(report[c][d] for c in columns))
                    for d in sorted(common)
                    if start <= d and d.replace(day=1) <= end
                ]
        frame = pd.DataFrame(rows, columns=["series_id", "date", "value"])
        if frame.empty:
            return empty_observations()
        by_code = {seed.code: seed for seed, _ in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = frame["series_id"].map(lambda c: parse_remote(by_code[c].remote or "")[0])
        frame["entity_id"] = ""  # BKM publishes no bank-level figures
        frame["entity_name"] = None
        frame["metric"] = metric_name(0)
        frame["freq"] = frame["series_id"].map(lambda c: by_code[c].freq.value)
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c].unit)
        frame["currency"] = frame["series_id"].map(lambda c: by_code[c].currency)
        frame["is_proxy"] = False
        frame["vintage_ts"] = datetime.now(UTC)
        frame["publication_lag_days"] = frame["series_id"].map(self.source.lag_for)
        return frame.sort_values(["series_id", "date"], ignore_index=True)[OBSERVATION_COLUMNS]

    def healthcheck(self) -> HealthResult:
        if not self.source.canaries:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "katalogda canary seri yok")
        today, details, started = date.today(), [], time.monotonic()
        try:
            for seed in self.source.canaries:
                start = today - timedelta(days=_HEALTH_LOOKBACK_DAYS)
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
