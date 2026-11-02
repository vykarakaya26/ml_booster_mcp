"""BTK provider: quarterly operator data from the public "Üç Aylık Pazar Verileri Raporu" PDFs (keyless, anonymous).

Live findings 2026-11-02 (CLAUDE.md source rule 5):
- www.btk.gov.tr/robots.txt allows everything except /api/ and /_next/. The site has no separate terms of use page; the
  footer reads "Tüm Hakları Saklıdır". The report is an official statistics publication; it is quoted with attribution.
- The announcement page (/elektronik-haberlesme-pazar-verileri) is server-rendered HTML that lists one PDF per quarter
  ("2026-Q2 pdf - 40.9 MB") from 2009-Q1 on, linked as https://www.btk.gov.tr/s3/web-btk-site/<uuid>/<yyyy>/<mm>/<uuid>.pdf.
  There is no Excel or data annex; some quarters (2018-Q2, 2018-Q4) are missing. Recent reports are 40-45 MB.
- Recent reports are served as application/pdf, older uploads as application/octet-stream (the %PDF- signature is
  checked either way). The 2026-Q2 report went online on 7 Oct 2026 (Last-Modified), 99 days after the quarter end.
- Charts are raster images; only tables and the running text carry numbers. The tables used here have kept their
  layout since 2016 (the reports carry a text layer: InDesign/ABBYY/Adobe PDF):
  * Çizelge 1-3 "Türk Telekom ve Mobil İşletmecilerin Üç Aylık Net Satış Gelirleri, ₺": five quarters, rows
    TT Mobil (Avea in older reports), Turkcell, Türk Telekom, Vodafone and TOPLAM. The 2026-Q2 report prints one total
    with a digit missing ("109.741.470.17"); such totals are replaced by the sum of the operators.
  * Mobile subscriber shares: only a chart (Şekil 4-13) plus one sentence of the text, worded the same since 2016:
    "... itibarıyla abone sayısına göre Turkcell'in %39,1, Vodafone'un %28,8 ve TT Mobil'in ise %32'lik paya ...".
  * Çizelge 3-2 "Sabit Genişbant İşletmecilerin (İSS) Pazar Payları": subscriber and revenue shares (%) of the
    largest fixed broadband operators plus "Diğer" and TOPLAM = 100, current quarter only.
  * Çizelge 3-1 "Toplam İnternet Abone Sayıları": subscribers by technology for the same quarter a year ago, the
    previous quarter and the current quarter (the caption is missing from the text layer of some reports).
- Every table is validated before it is written: operators add up to the printed total, shares to 100 ± 0.5. A table
  that cannot be found or fails validation is skipped with a warning; the other tables of the report are still used.

Each report file is saved to the bronze store; its URL changes with every upload, so a file already in the bronze store
(same URL, intact content hash) is not downloaded again. For a period, only the reports needed are downloaded: the
fewest reports that cover the period (Çizelge 1-3 lists five quarters, so one report in five is enough), the last
quarter from the newest report.

The catalog `remote` field is `<table>/<column>`: `gelir/net_satis`, `mobil_abone_pay/abone`,
`sabit_genisbant_pay/abone`, `sabit_genisbant_pay/gelir`, `internet_abone/toplam` or `internet_abone/fiber`.
Requests go only to the host pinned below.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

import pandas as pd
import pypdfium2 as pdfium  # installed with pdfplumber; its PDFium text layer reads a 45 MB report in < 1 s

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

log = logging.getLogger(__name__)

HOST = "www.btk.gov.tr"
INDEX_PATH = "/elektronik-haberlesme-pazar-verileri"
REPORT_PATH = re.compile(r"/s3/[A-Za-z0-9/_-]{1,200}\.pdf")
SHARE_TOLERANCE = 0.5  # percentage points; the report rounds shares to one or two decimals
_HEALTH_LOOKBACK_DAYS = 200
_STALENESS_MARGIN_DAYS = 30

_TR_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "IIiSsGgUuOoCc")
_ANCHOR = re.compile(r"<a\s[^>]*?href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S | re.I)
_QUARTER_LABEL = re.compile(r"\b(\d{4})-Q([1-4])\b")
_PERIOD = re.compile(r"^(\d{4})-([1-4])$")
_PERIOD_ANY = re.compile(r"\b(\d{4})-([1-4])\b")
_THOUSANDS = re.compile(r"^\d{1,3}(?:\.\d{3})*$")
_SHARE_ROW = re.compile(r"^(?P<a>\S.*?)\s+(?P<va>\d{1,3}[,.]\d{1,2})\s+(?P<b>\S.*?)\s+(?P<vb>\d{1,3}[,.]\d{1,2})$")
_MOBILE_SENTENCE = re.compile(r"abone sayısına göre(?P<body>.{0,300}?)pay(?:a|ına) sahip", re.S)
_MOBILE_SHARE = re.compile(r"(?P<name>Turkcell|Vodafone|TT\s?Mobil|Avea)\S*\s(?:[^%]{0,60}?)%\s?(?P<value>\d{1,2}(?:,\d{1,2})?)")

# Stable entity ids and display names; the report spells some operators differently over time (Avea → TT Mobil).
MOBILE_OPERATORS = {"TURKCELL": "Turkcell", "VODAFONE": "Vodafone", "TT_MOBIL": "TT Mobil"}
REVENUE_OPERATORS = {**MOBILE_OPERATORS, "TURK_TELEKOM": "Türk Telekom"}
_ALIASES = {"AVEA": "TT_MOBIL", "TTMOBIL": "TT_MOBIL", "D_SMART": "DSMART", "TURK_NET": "TURKNET"}
_DISPLAY = {"TTNET": "TTNet", "SUPERONLINE": "Superonline", "TURKSAT": "Türksat", "TURKNET": "TurkNet",
            "VODAFONE_NET": "Vodafone Net", "DSMART": "D-Smart", "MILLENICOM": "Millenicom", "DIGER": "Diğer",
            "DIGITAL_PLATFORM": "Digital Platform", "ANDROMEDA_TV": "Andromeda TV"}  # fmt: skip
_TOTAL_KEYS = {"TOPLAM", "TOTAL"}


def entity_key(label: str) -> str:
    """Stable entity id of an operator label: ASCII upper case, aliases resolved ("Avea" → TT_MOBIL)."""
    key = re.sub(r"[^A-Z0-9]+", "_", label.translate(_TR_FOLD).upper()).strip("_")
    return _ALIASES.get(key, key)


def display_name(key: str, label: str) -> str:
    return REVENUE_OPERATORS.get(key) or _DISPLAY.get(key) or " ".join(label.split())[:60]


def quarter_end(year: int, quarter: int) -> date:
    return date(year, 12, 31) if quarter == 4 else date(year, 3 * quarter + 1, 1) - timedelta(days=1)


def _quarter_index(d: date) -> int:
    return d.year * 4 + (d.month - 1) // 3


def _fold(text: str) -> str:
    return " ".join(text.translate(_TR_FOLD).upper().split())


def _thousands(token: str) -> float | None:
    return float(token.replace(".", "")) if _THOUSANDS.match(token) else None


def _percent(token: str) -> float:
    return float(token.replace(",", "."))  # the 2025-Q2 report prints shares with a decimal point


# Rows: (period end, entity id, entity name, value); the total has the entity id "".
Row = tuple[date, str, str, float]


def parse_revenue(lines: list[str]) -> list[Row]:
    """Çizelge 1-3: quarterly net sales of Türk Telekom and the mobile operators, TL. The first table whose header is
    a row of quarters and whose caption (the lines above it) speaks of net sales; the investment table that follows
    it has the same layout and is skipped by its caption."""
    for i, line in enumerate(lines):
        tokens = line.split()
        if len(tokens) < 2 or not all(_PERIOD.match(t) for t in tokens):
            continue
        caption = _fold(" ".join(lines[max(0, i - 4) : i]))
        if "NET SATIS GELIR" not in caption or "YATIRIM" in caption:
            continue
        periods = [quarter_end(int(m[1]), int(m[2])) for m in map(_PERIOD.match, tokens) if m]
        table: dict[str, list[float | None]] = {}
        for row in lines[i + 1 : i + 8]:
            parts = row.split()
            label, values = " ".join(parts[: len(parts) - len(periods)]), parts[len(parts) - len(periods) :]
            key = entity_key(label)
            if key not in REVENUE_OPERATORS and key not in _TOTAL_KEYS:
                break
            table[key] = [_thousands(v) for v in values]
        if set(REVENUE_OPERATORS) - set(table):
            log.warning("BTK revenue table without the expected operators: %s", sorted(table))
            return []
        return _validated_revenue(periods, table)
    return []


def _validated_revenue(periods: list[date], table: dict[str, list[float | None]]) -> list[Row]:
    rows: list[Row] = []
    totals = table.get("TOPLAM") or table.get("TOTAL") or [None] * len(periods)
    for j, period in enumerate(periods):
        values = {k: table[k][j] for k in REVENUE_OPERATORS}
        if any(v is None for v in values.values()):
            log.warning("BTK revenue table: unreadable value for %s, quarter skipped", period)
            continue
        listed = sum(v for v in values.values() if v is not None)
        total = totals[j]
        if total is None:
            log.warning("BTK revenue table: total for %s unreadable, sum of the operators used", period)
            total = listed
        elif abs(total - listed) > max(2.0, 1e-9 * total):
            log.warning("BTK revenue table: total for %s (%s) differs from the sum of the operators (%s), quarter skipped",
                        period, total, listed)  # fmt: skip
            continue
        rows.append((period, "", "TOPLAM", total))
        rows += [(period, k, REVENUE_OPERATORS[k], v) for k, v in values.items() if v is not None]
    return rows


def parse_mobile_shares(text: str, period: date) -> list[Row]:
    """Mobile operators' shares of subscribers (%) from the sentence of the mobile market section; the executive summary repeats
    it in a multi-column layout whose text order is scrambled, so the first sentence that names all three operators
    with shares adding up to 100 is used."""
    flat = " ".join(text.split())
    for m in _MOBILE_SENTENCE.finditer(flat):
        shares: dict[str, float] = {}
        for s in _MOBILE_SHARE.finditer(m["body"]):
            shares.setdefault(entity_key(s["name"]), _percent(s["value"]))
        if set(shares) == set(MOBILE_OPERATORS) and abs(sum(shares.values()) - 100) <= SHARE_TOLERANCE:
            return [(period, "", "TOPLAM", 100.0)] + [(period, k, MOBILE_OPERATORS[k], v) for k, v in shares.items()]
    log.warning("BTK report %s: mobile subscriber share sentence not found", period)
    return []


def parse_fixed_broadband_shares(lines: list[str], period: date) -> dict[str, list[Row]]:
    """Çizelge 3-2: shares (%) of fixed broadband operators by subscribers (`abone`) and revenue (`gelir`)."""
    for i, line in enumerate(lines):
        head = _fold(line)
        if not ("ISLETMECI ABONE" in head and "GELIR" in head):
            continue
        sides: dict[str, list[tuple[str, float]]] = {"abone": [], "gelir": []}
        totals: dict[str, float] = {}
        for row in lines[i + 1 : i + 25]:
            m = _SHARE_ROW.match(row.strip())
            if m is None:
                break
            for side, (label, value) in zip(sides, ((m["a"], m["va"]), (m["b"], m["vb"])), strict=True):
                if entity_key(label) in _TOTAL_KEYS:
                    totals[side] = _percent(value)
                else:
                    sides[side].append((label, _percent(value)))
            if len(totals) == 2:
                break
        out: dict[str, list[Row]] = {}
        for side, entries in sides.items():
            listed = sum(v for _, v in entries)
            keys = [entity_key(label) for label, _ in entries]
            # the TOPLAM row is missing from some reports (2025-Q1); when printed it must be 100
            if abs(totals.get(side, 100) - 100) > 0.01 or abs(listed - 100) > SHARE_TOLERANCE or len(set(keys)) != len(keys):
                log.warning("BTK fixed broadband shares (%s, %s) failed validation: sum %.2f", side, period, listed)
                continue
            out[side] = [(period, "", "TOPLAM", 100.0)] + [
                (period, k, display_name(k, label), v) for k, (label, v) in zip(keys, entries, strict=True)
            ]
        return out
    log.warning("BTK report %s: fixed broadband share table not found", period)
    return {}


def parse_internet_subscribers(lines: list[str]) -> dict[str, list[Row]]:
    """Çizelge 3-1: internet (broadband) subscribers, total (`toplam`) and fiber (`fiber`), for the three quarters
    of the table header. The table is located by its xDSL row; its caption is not always in the text layer."""
    for i, line in enumerate(lines):
        if not line.startswith("xDSL "):
            continue
        header = next((h for h in reversed(lines[max(0, i - 12) : i]) if len(_PERIOD_ANY.findall(h)) >= 3), None)
        if header is None:
            continue
        periods = [quarter_end(int(y), int(q)) for y, q in _PERIOD_ANY.findall(header)[:3]]
        found: dict[str, list[float]] = {}
        for row in lines[i : i + 30]:
            key = entity_key(row.split(" ")[0])
            name = "fiber" if row.startswith("Fiber (Toplam)") else "toplam" if key in _TOTAL_KEYS else None
            if name is None or name in found:
                continue
            numbers = [v for v in map(_thousands, row.split()) if v is not None][:3]
            if len(numbers) == 3:
                found[name] = numbers
            if len(found) == 2:
                break
        if len(found) != 2 or any(f >= t for f, t in zip(found["fiber"], found["toplam"], strict=True)):
            log.warning("BTK internet subscriber table failed validation")
            return {}
        return {name: [(p, "", "TOPLAM", v) for p, v in zip(periods, values, strict=True)] for name, values in found.items()}
    log.warning("BTK report: internet subscriber table not found")
    return {}


@dataclass(frozen=True)
class Report:
    period: date  # quarter end
    url: str


@dataclass(frozen=True)
class TableSpec:
    window: int  # how many quarters back (including its own) one report covers
    parse: Callable[[list[str], str, date], dict[str, list[Row]]]  # (lines, full text, report quarter) → column → rows


TABLES: dict[str, TableSpec] = {
    "gelir": TableSpec(5, lambda lines, text, q: {"net_satis": parse_revenue(lines)}),
    "mobil_abone_pay": TableSpec(1, lambda lines, text, q: {"abone": parse_mobile_shares(text, q)}),
    "sabit_genisbant_pay": TableSpec(1, lambda lines, text, q: parse_fixed_broadband_shares(lines, q)),
    "internet_abone": TableSpec(2, lambda lines, text, q: parse_internet_subscribers(lines)),
}
_COLUMNS = {"gelir": {"net_satis"}, "mobil_abone_pay": {"abone"}, "sabit_genisbant_pay": {"abone", "gelir"},
            "internet_abone": {"toplam", "fiber"}}  # fmt: skip
_REMOTE = re.compile(r"^(?P<table>[a-z_]{1,30})/(?P<column>[a-z_]{1,20})$")


def parse_remote(remote: str) -> tuple[str, str]:
    """(table, column) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    if m is None or m["table"] not in TABLES or m["column"] not in _COLUMNS[m["table"]]:
        known = ", ".join(f"{t}/{c}" for t, cols in _COLUMNS.items() for c in sorted(cols))
        raise InvalidInput(f"BTK remote alanı şu değerlerden biri olmalıdır: {known} (verilen: {remote!r})")
    return m["table"], m["column"]


def parse_index(page: str) -> list[Report]:
    """Report links of the announcement page, newest quarter first; links to other hosts are ignored."""
    reports: dict[date, str] = {}
    for href, body in _ANCHOR.findall(page):
        label = _QUARTER_LABEL.search(re.sub(r"<[^>]+>", " ", body))
        url = html.unescape(href)
        parts = urlsplit(url if "://" in url else f"https://{HOST}{url}")
        if label is None or parts.scheme != "https" or parts.hostname != HOST or not REPORT_PATH.fullmatch(parts.path):
            continue
        reports.setdefault(quarter_end(int(label[1]), int(label[2])), f"https://{HOST}{parts.path}")
    return [Report(p, u) for p, u in sorted(reports.items(), reverse=True)]


def select_reports(reports: list[Report], start: date, end: date, window: int) -> list[Report]:
    """Fewest reports that cover the quarters of [start, end]. The last quarter is read from the newest report that
    still lists it (later reports carry revised figures); earlier quarters from the report that reaches furthest back."""
    by_index = {_quarter_index(r.period): r for r in reports}
    first, q = _quarter_index(start), _quarter_index(end)
    chosen: dict[int, Report] = {}
    while q >= first:
        span = range(q, q + window)  # reports that list quarter q
        candidates = [i for i in (reversed(span) if not chosen else span) if i in by_index]
        if not candidates:
            q -= 1
            continue
        chosen[candidates[0]] = by_index[candidates[0]]
        q = candidates[0] - window  # the report covers quarters pick-window+1 .. pick
    return [chosen[i] for i in sorted(chosen)]


def pdf_text(content: bytes) -> list[str]:
    """Text layer of every page of a PDF (PDFium; no rendering)."""
    try:
        document = pdfium.PdfDocument(content)
    except pdfium.PdfiumError:
        raise UnexpectedResponse("BTK raporu PDF olarak açılamadı") from None
    try:
        pages = []
        for page in document:
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n"))
            textpage.close()
            page.close()
        return pages
    finally:
        document.close()


class BtkProvider:
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
        self._index_url = f"https://{HOST}{INDEX_PATH}"
        self._http = http
        self._raw_store = raw_store

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is None or not seed.remote:
            known = ", ".join(s.code for s in self.source.series)
            raise InvalidInput(f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}")
        return seed

    def reports(self) -> list[Report]:
        response = self._http.get(self._index_url, headers={"Accept": "text/html"})
        self._check(response, ("text/html",))
        reports = parse_index(response.content.decode("utf-8", errors="replace"))
        if not reports:
            raise UnexpectedResponse(f"BTK duyuru sayfasında rapor bağlantısı bulunamadı: {self._index_url}")
        return reports

    def _download(self, report: Report) -> bytes:
        if self._raw_store is not None and (cached := self._raw_store.load(self.source.id, report.url)) is not None:
            return cached
        response = self._http.get(report.url, headers={"Accept": "application/pdf"})
        self._check(response, ("application/pdf", "application/octet-stream"))  # older uploads are octet-stream
        if not response.content.startswith(b"%PDF-"):
            raise UnexpectedResponse(f"BTK raporu PDF değil: {report.url}")
        if self._raw_store is not None:  # the bronze copy (content hash) doubles as change detection
            self._raw_store.save(self.source.id, report.url, response.content, "application/pdf")
        return response.content

    def _check(self, response: HttpResponse, expected: tuple[str, ...]) -> None:
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"BTK {response.status} yönlendirmesi döndürdü: {response.url}")
        if response.status != 200:
            raise ProviderError(f"BTK HTTP {response.status}: {response.url}")
        if not any(e in response.content_type for e in expected):
            raise UnexpectedResponse(f"BTK {expected[0]} yerine başka bir içerik döndürdü ({response.content_type[:40]}): {response.url}")

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
        tables = {table for _, (table, _) in seeds}
        window = min(TABLES[t].window for t in tables)
        reports = select_reports(self.reports(), request.period.start, request.period.end, window)
        vintage = datetime.now(UTC)
        latest: dict[tuple[str, str, date], tuple[date, str, float]] = {}  # newest report wins for every observation
        for report in reports:
            pages = pdf_text(self._download(report))
            lines, text = report_lines(pages), "\n".join(pages)
            for table in sorted(tables):
                parsed = TABLES[table].parse(lines, text, report.period)
                for seed, (t, column) in seeds:
                    if t != table:
                        continue
                    for period, entity, name, value in parsed.get(column, []):
                        if request.period.start <= period <= request.period.end:
                            key = (seed.code, entity, period)
                            if key not in latest or latest[key][0] < report.period:
                                latest[key] = (report.period, name, value)
        rows = [(code, entity, name, period, value) for (code, entity, period), (_, name, value) in latest.items()]
        frame = pd.DataFrame(rows, columns=["series_id", "entity_id", "entity_name", "date", "value"])
        if frame.empty:
            return empty_observations()
        by_code = {seed.code: (seed, table) for seed, (table, _) in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = frame["series_id"].map(lambda c: f"pazar_verileri_{by_code[c][1]}")
        frame["metric"] = metric_name(0)
        frame["freq"] = frame["series_id"].map(lambda c: by_code[c][0].freq.value)
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c][0].unit)
        frame["currency"] = frame["series_id"].map(lambda c: by_code[c][0].currency)
        frame["is_proxy"] = False  # the report covers every operator in the market
        frame["vintage_ts"] = vintage
        frame["publication_lag_days"] = frame["series_id"].map(self.source.lag_for)
        _warn_jumps(frame)
        return frame.sort_values(["series_id", "entity_id", "date"], ignore_index=True)[OBSERVATION_COLUMNS]

    def healthcheck(self) -> HealthResult:
        if not self.source.canaries:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "katalogda canary seri yok")
        today, details, started = date.today(), [], time.monotonic()
        canaries = self.source.canaries
        try:  # one request for every canary: they share the newest report
            start = today - timedelta(days=_HEALTH_LOOKBACK_DAYS)
            df = self.fetch(FetchRequest.create(self.source.id, [s.code for s in canaries], start, today))
            for seed in canaries:
                total = df[(df["series_id"] == seed.code) & (df["entity_id"] == "")]
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


def _warn_jumps(frame: pd.DataFrame, limit: float = 10.0) -> None:
    """Consistency with the previous period: a share that moves more than `limit` points in one quarter is logged
    (a misread table is the usual cause; real market moves of that size are rare)."""
    shares = frame[(frame["unit"] == "percent") & (frame["entity_id"] != "")].sort_values("date")
    for (code, entity), part in shares.groupby(["series_id", "entity_id"]):
        jumps = part["value"].diff().abs()
        for i in jumps[jumps > limit].index:
            log.warning("BTK %s/%s: share moved %.1f points in one quarter (%s)", code, entity, jumps[i], part.loc[i, "date"])


def report_lines(pages: Iterable[str]) -> list[str]:
    """Non-empty, stripped lines of the report pages (the form the table parsers take)."""
    return [line.strip() for page in pages for line in page.split("\n") if line.strip()]
