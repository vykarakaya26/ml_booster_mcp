"""EPDK electricity tariff tables: regulated end-user tariff components by subscriber group (keyless).

Live findings 2026-11-15:
- The page "Elektrik Faturalarına Esas Tarife Tabloları" (/Detay/Icerik/3-1327/...) fills its list with
  `POST /Detay/GetFastAccessList` (JSON `{"fId": "1327"}`) → `{"State": 1, "model": [...]}`. Every entry is one Board
  decision: `Date` (decision date, dd.mm.yyyy), `Number`, `RgDate` (Official Gazette, sometimes empty), `Title`
  ("4/4/2026'dan itibaren geçerli olacak elektrik tarifeleri tabloları hakkında") and `FastAccessDetail` files
  (`ContentId` = document id for /Detay/DownloadDocument, `ContentType` 9 = Excel, 11/8 = decision text). No robots.txt
  (302 to the home page), no terms page; the tables are public and need no login. Only the host below is contacted.
- Excel tables exist for every decision since 2013-12 (older ones are .xls); since 2016-01 the layout is stable:
  sheet "Faaliyet Bazlı Tarife Tablosu" (energy components: single-rate, gündüz, puant, gece; distribution fee) plus
  "Nihai Tarife Tablosu" (power fee of double-term tariffs, reactive energy) until 2025-04; from 2026-04 one sheet
  "Tarife Tablosu" holds all components. Values are kuruş per kWh (power fee: kuruş per kW per month), taxes, funds
  and levies excluded ("Fon, pay, vergi vb. yasal yükümlülükler ayrıca ilave edilir"); the tables publish no tax
  component, so none is written. Row labels: "Ticarethane" became "Kamu ve Özel Hizmetler Sektörü ile Diğer" and
  "Tarımsal Sulama" "Tarımsal Faaliyetler" (2022-06); low-voltage households are split into a low and a high tier
  from 2022-01 (5, 7, then 8 kWh/day), low-voltage commercial users from 2022-03 (30 kWh/day).
- Three-time blocks (note under every table since 2016): gündüz 06-17, puant 17-22, gece 22-06 for meters on
  permanent summer time.
- Some decisions are dated after the day their tariff applies from (30.01.2021 for 1/1/2021, 01.04.2015 for
  1/1/2015); green tariff decisions ("yeşil tarife") carry only the green tariff sheet and are skipped.

Point-in-time rule: the value of month M is the tariff in force on the last day of M according to the decisions
taken by that day (largest effective date among decisions with effective date and decision date ≤ month end). A
tariff therefore enters the month it becomes known, never earlier; a retroactive tariff is not written back. Only
complete months (month end ≤ today) are written; the catalog lag is 0 because the decision precedes the month end.

Catalog `remote`: `<SECTION>.<GROUP>[,<GROUP>...]/<COMPONENT>`. SECTION: ILETIM, OG_CIFT, OG_TEK, AG_TEK. GROUP:
TUKETICI (transmission users), SANAYI, KAMU_OZEL (ticarethane), MESKEN, TARIMSAL, AYDINLATMA, GENEL_AYDINLATMA,
SEHIT_GAZI, with `_DUSUK` / `_YUKSEK` for the tiers; the first group that the month's table has is used (e.g.
`AG_TEK.MESKEN,MESKEN_DUSUK`). COMPONENT: ENERJI_TEK, ENERJI_GUNDUZ, ENERJI_PUANT, ENERJI_GECE, DAGITIM, GUC, REAKTIF.
The seed unit sets the scale: try_per_kwh / try_per_mwh (energy, distribution), try_per_kw_month (GUC),
try_per_kvarh (REAKTIF); the published kuruş values are divided by 100 (×10 for TL/MWh).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlsplit

import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.providers.epdk_reports import Grid, fold, number, office_kind, read_xlsx
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

log = logging.getLogger(__name__)

HOST = "www.epdk.gov.tr"
LIST_PATH = "/Detay/GetFastAccessList"
LIST_ID = "1327"  # "Elektrik Faturalarına Esas Tarife Tabloları"
DOWNLOAD_PATH = "/Detay/DownloadDocument"
EXCEL_CONTENT_TYPE = 9
FIRST_EFFECTIVE = date(2016, 1, 1)  # stable Excel layout from here on
MAX_DECISIONS = 500
SECTIONS = frozenset({"ILETIM", "OG_CIFT", "OG_TEK", "AG_TEK"})
GROUPS = frozenset({
    "TUKETICI", "SANAYI", "KAMU_OZEL", "MESKEN", "TARIMSAL", "AYDINLATMA", "GENEL_AYDINLATMA", "SEHIT_GAZI",
    "KAMU_OZEL_DUSUK", "KAMU_OZEL_YUKSEK", "MESKEN_DUSUK", "MESKEN_YUKSEK",
})  # fmt: skip
ENERGY = ("ENERJI_TEK", "ENERJI_GUNDUZ", "ENERJI_PUANT", "ENERJI_GECE")
COMPONENTS = frozenset({*ENERGY, "DAGITIM", "GUC", "REAKTIF"})
# Seed unit → factor applied to the published kuruş value, and the components the unit fits.
UNITS: dict[str, tuple[float, frozenset[str]]] = {
    "try_per_kwh": (0.01, frozenset({*ENERGY, "DAGITIM"})),
    "try_per_mwh": (10.0, frozenset({*ENERGY, "DAGITIM"})),
    "try_per_kw_month": (0.01, frozenset({"GUC"})),
    "try_per_kvarh": (0.01, frozenset({"REAKTIF"})),
}
# Plausibility bounds of a published value (kuruş): a misread column would fall outside.
_MIN_KURUS, _MAX_KURUS = 0.0, 1_000_000.0
_HEALTH_LOOKBACK_DAYS = 120
_DOC_ID = re.compile(r"^[A-Za-z0-9+/=]{4,64}$")
_DAY = re.compile(r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*$")
_TITLE_DATE = re.compile(r"(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4})")
_TITLE_QUARTER = re.compile(r"(20\d{2})\s*(?:YILI\s*)?([1-4])\s*CEYREK")
_REMOTE = re.compile(r"^(?P<section>[A-Z_]{4,10})\.(?P<groups>[A-Z_]{3,20}(?:,[A-Z_]{3,20}){0,3})/(?P<component>[A-Z_]{3,16})$")


def parse_remote(remote: str) -> tuple[str, tuple[str, ...], str]:
    """(section, groups in order of preference, component) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    groups = tuple(m["groups"].split(",")) if m else ()
    if m is None or m["section"] not in SECTIONS or not set(groups) <= GROUPS or m["component"] not in COMPONENTS:
        raise InvalidInput(
            "EPDK tarife remote alanı '<BÖLÜM>.<ABONE GRUBU>/<BİLEŞEN>' biçiminde olmalıdır "
            f"(bölümler: {sorted(SECTIONS)}; bileşenler: {sorted(COMPONENTS)}): {remote!r}"
        )
    return m["section"], groups, m["component"]


@dataclass(frozen=True)
class Decision:
    """One Board decision with tariff tables."""

    decided: date
    effective: date
    number: str
    title: str
    doc_ids: tuple[str, ...]  # Excel files


@dataclass
class TariffTable:
    """Published values (kuruş) keyed by (section, group) → component."""

    values: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)

    def get(self, section: str, groups: tuple[str, ...], component: str) -> float | None:
        for group in groups:
            row = self.values.get((section, group))
            if row is not None and component in row:
                return row[component]
        return None


def _day(text: object) -> date | None:
    m = _DAY.fullmatch(str(text or ""))
    if m is None:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def effective_date(title: str) -> date | None:
    """First date in the title ("1/7/2024'ten itibaren"), else the start of a named quarter ("2018 1. Çeyrek")."""
    if m := _TITLE_DATE.search(title):
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    if m := _TITLE_QUARTER.search(fold(title)):
        return date(int(m[1]), 3 * int(m[2]) - 2, 1)
    return None


def parse_decisions(doc: object) -> list[Decision]:
    """Tariff decisions of the list JSON with an effective date and at least one Excel file (green-tariff-only
    decisions are skipped), sorted by effective date."""
    if not isinstance(doc, dict) or doc.get("State") != 1 or not isinstance(doc.get("model"), list):
        raise UnexpectedResponse("EPDK tarife listesi beklenen biçimde değil (State/model alanları yok)")
    entries = doc["model"]
    if len(entries) > MAX_DECISIONS:
        raise UnexpectedResponse("EPDK tarife listesi beklenenden uzun")
    decisions = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = " ".join(str(entry.get("Title") or "").split())
        decided, effective = _day(entry.get("Date")), effective_date(title)
        files = entry.get("FastAccessDetail") if isinstance(entry.get("FastAccessDetail"), list) else []
        doc_ids = tuple(
            str(f["ContentId"]) for f in files
            if isinstance(f, dict) and f.get("ContentType") == EXCEL_CONTENT_TYPE and _DOC_ID.fullmatch(str(f.get("ContentId", "")))
        )  # fmt: skip
        if decided is None or effective is None or not doc_ids or "YESIL TARIFE" in fold(title):
            continue
        decisions.append(Decision(decided, effective, str(entry.get("Number") or "").strip(), title, doc_ids))
    if not decisions:
        raise UnexpectedResponse("EPDK tarife listesinde tarife tablosu içeren karar bulunamadı (sayfa yapısı değişmiş olabilir)")
    return sorted(decisions, key=lambda d: (d.effective, d.decided))


def in_force(decisions: list[Decision], day: date) -> Decision | None:
    """The decision whose tariff applies on `day` as known on `day`: largest effective date among decisions taken
    and in effect by then (a later decision for the same effective date replaces the earlier one)."""
    known = [d for d in decisions if d.effective <= day and d.decided <= day]
    return max(known, key=lambda d: (d.effective, d.decided)) if known else None


# --- Table parsing ---------------------------------------------------------------------------------------------


def _component(header: str, *, power_sheet: bool) -> str | None:
    """Component of a header cell; None for cells that are not one (or belong to the combined-total block)."""
    h = fold(header)
    if power_sheet:  # "Nihai Tarife Tablosu": only the power fee and reactive energy are read from it
        if h.startswith("GUC BEDELI"):
            return "GUC"
        return "REAKTIF" if h.startswith("REAKTIF") else None
    if "TEK ZAMANLI" in h and "COK ZAMANLI" not in h:
        return "ENERJI_TEK"
    for word, component in (("GUNDUZ", "ENERJI_GUNDUZ"), ("PUANT", "ENERJI_PUANT"), ("GECE", "ENERJI_GECE")):
        if h.startswith(word) or h.startswith(f"PERAKENDE {word}"):
            return component
    if h.startswith("DAGITIM BEDELI"):
        return "DAGITIM"
    if h.startswith("GUC BEDELI") and "HARIC" not in h:
        return "GUC"
    return "REAKTIF" if h.startswith("REAKTIF") else None


def _stops_block(header: str, *, power_sheet: bool) -> bool:
    """Right-hand blocks that repeat the components for other users: combined totals ("Güç Bedeli Hariç Toplam
    Tarifeler") and, on the power sheet, users supplied by other suppliers ("Özel tedarikçiden ...")."""
    h = fold(header)
    return "OZEL TEDARIKCI" in h if power_sheet else "TOPLAM" in h


def _columns(grid: Grid, *, power_sheet: bool) -> dict[int, str]:
    """Column → component from the header cells above the first numeric data row."""
    columns: dict[int, str] = {}
    stop = None
    for row in grid.rows:
        if any(isinstance(c, int | float) and not isinstance(c, bool) for c in row) and columns:
            break
        for i, cell in enumerate(row):
            if not isinstance(cell, str) or (stop is not None and i >= stop):
                continue
            if _stops_block(cell, power_sheet=power_sheet):
                stop = i if stop is None else min(stop, i)
                continue
            if (component := _component(cell, power_sheet=power_sheet)) is not None:
                columns.setdefault(i, component)
    return {i: c for i, c in columns.items() if stop is None or i < stop}


def _group(label: str) -> str | None:
    f = fold(label)
    tier = "_DUSUK" if "VE ALTI" in f else "_YUKSEK" if "USTU" in f else ""
    if f.startswith("SANAYI"):
        return "SANAYI"
    if f.startswith("TICARETHANE") or f.startswith("KAMU VE OZEL HIZMETLER"):
        return f"KAMU_OZEL{tier}"
    if f.startswith("MESKEN"):
        return f"MESKEN{tier}"
    if f.startswith("TARIMSAL"):
        return "TARIMSAL"
    if f.startswith("GENEL AYDINLATMA"):
        return "GENEL_AYDINLATMA"
    if f.startswith("AYDINLATMA"):
        return "AYDINLATMA"
    if f.startswith("SEHIT AILELERI"):
        return "SEHIT_GAZI"
    if f == "TUKETICI":
        return "TUKETICI"
    return None


@dataclass
class _Context:
    section: str | None = None
    voltage: str | None = None
    term: str | None = None

    def mark(self, label: str) -> bool:
        """Update the context from a section label; True if the label was one."""
        f = fold(label)
        if "URETICI" in f:  # producers (and their distribution tariffs) are not end users
            self.section, self.voltage, self.term = "URETICI", None, None
            return True
        if "ILETIM SISTEMI" in f:
            self.section, self.voltage, self.term = "ILETIM", None, None
            return True
        if f.startswith("DAGITIM SISTEMI") or f.startswith("GOREVLI TEDARIK"):
            self.section = None if self.section in {"ILETIM", "URETICI"} or f.startswith("DAGITIM") else self.section
            return True
        marked = False
        if f.startswith("OG ") or f == "OG" or "ORTA GERILIM" in f:
            self.voltage, marked = "OG", True
        elif f.startswith("AG ") or f == "AG" or "ALCAK GERILIM" in f:
            self.voltage, marked = "AG", True
        if "CIFT TERIM" in f:
            self.term, marked = "CIFT", True
        elif "TEK TERIM" in f:
            self.term, marked = "TEK", True
        if marked and self.voltage:
            self.section = f"{self.voltage}_{self.term}" if self.term else None
        return marked

    def key(self) -> str | None:
        return self.section if self.section in SECTIONS else None


def _value(cell: object) -> float | None:
    """Numeric cell value; header and note text in a component column is not a value."""
    if isinstance(cell, str) and not re.search(r"[0-9]", cell):
        return None
    try:
        return number(cell)
    except UnexpectedResponse:
        if isinstance(cell, str):
            return None
        raise


def read_grid(grid: Grid, *, power_sheet: bool = False) -> TariffTable:
    columns = _columns(grid, power_sheet=power_sheet)
    table = TariffTable()
    if not columns:
        return table
    first = min(columns)
    ctx = _Context()
    for row in grid.rows:
        labels = [c for c in row[:first] if isinstance(c, str) and c.strip()]
        group = None
        for label in labels:
            if not ctx.mark(label):
                group = _group(label) or group
        values = {}
        for i, component in columns.items():
            if i < len(row) and (v := _value(row[i])) is not None:
                values[component] = v
        if not values:
            continue
        if group is None and ctx.section == "ILETIM" and labels:
            group = "TUKETICI"  # 2026 layout: the transmission row is labelled with its section only
        section = ctx.key()
        if group is None or section is None:
            continue
        if section == "ILETIM" and group != "TUKETICI":
            continue
        for component, v in values.items():
            if not _MIN_KURUS <= v <= _MAX_KURUS:
                raise UnexpectedResponse(f"EPDK tarife tablosunda olağan dışı değer: {section}.{group}/{component} = {v:g}")
        table.values.setdefault((section, group), {}).update(values)
    return table


def tariff_table(sheets: list[tuple[str, Grid]], label: str) -> TariffTable:
    """Components of one decision's workbook (main sheet plus, in the 2016-2025 layout, the power-fee sheet)."""
    main = next((g for t, g in sheets if fold(t).startswith("FAALIYET BAZLI")), None)
    if main is None:
        main = next((g for t, g in sheets if fold(t) == "TARIFE TABLOSU"), None)
    if main is None:
        raise UnexpectedResponse(f"EPDK tarife dosyasında tarife tablosu sayfası yok ({label})")
    table = read_grid(main)
    missing = {*ENERGY, "DAGITIM"} - {c for row in table.values.values() for c in row}
    if missing or ("AG_TEK", "SANAYI") not in table.values:
        raise UnexpectedResponse(f"EPDK tarife tablosu okunamadı ({label}); eksik bileşenler: {sorted(missing)}")
    power = next((g for t, g in sheets if fold(t).startswith("NIHAI TARIFE")), None)
    if power is not None:
        for key, row in read_grid(power, power_sheet=True).values.items():
            if key in table.values:
                for component, v in row.items():
                    table.values[key].setdefault(component, v)
    return table


# --- Provider --------------------------------------------------------------------------------------------------


def _month_end(year: int, month: int) -> date:
    return date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)


def _month_ends(start: date, end: date) -> list[date]:
    out, y, m = [], max(start, FIRST_EFFECTIVE).year, max(start, FIRST_EFFECTIVE).month
    while (last := _month_end(y, m)) <= end:
        out.append(last)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class EpdkTariffProvider:
    def __init__(
        self,
        source: Source,
        *,
        http: SecureHttpClient,
        secrets: EnvSecretProvider | None = None,
        raw_store: RawStore | None = None,
        today=date.today,
    ):
        parts = urlsplit(source.base_url)
        if parts.scheme != "https" or parts.hostname != HOST:
            raise ProviderError(f"{source.id} provider'ı yalnızca https://{HOST} ile çalışır")
        self.source = source
        self._http = http
        self._raw_store = raw_store
        self._today = today
        self._tables: dict[tuple[str, ...], TariffTable] = {}

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is None or not seed.remote:
            known = ", ".join(s.code for s in self.source.series)
            raise InvalidInput(f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}")
        return seed

    def _checked(self, response: HttpResponse, url: str) -> HttpResponse:
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"EPDK {response.status} yönlendirmesi döndürdü: {url}")
        if response.status != 200:
            raise ProviderError(f"EPDK HTTP {response.status}: {url}")
        return response

    def decisions(self) -> list[Decision]:
        url = f"https://{HOST}{LIST_PATH}"
        response = self._checked(self._http.post_json(url, {"fId": LIST_ID}, headers={"Accept": "application/json"}), url)
        if "json" not in response.content_type or response.content.lstrip()[:1] == b"<":
            raise UnexpectedResponse(f"EPDK tarife listesi JSON değil ({response.content_type[:40]}): {url}")
        try:
            doc = json.loads(response.content)
        except ValueError:
            raise UnexpectedResponse(f"EPDK tarife listesi çözümlenemedi: {url}") from None
        return parse_decisions(doc)

    def _table(self, decision: Decision) -> TariffTable:
        if (cached := self._tables.get(decision.doc_ids)) is not None:
            return cached
        label = f"{decision.number} sayılı karar, {decision.effective:%d.%m.%Y}"
        for doc_id in decision.doc_ids:
            url = f"https://{HOST}{DOWNLOAD_PATH}?id={quote(doc_id, safe='')}"
            response = self._checked(self._http.get(url), url)
            if response.content.lstrip()[:1] == b"<":
                raise UnexpectedResponse(f"EPDK dosya yerine HTML döndürdü: {url}")
            if office_kind(response.content) != "xlsx":  # binary .xls before 2014
                continue
            sheets = read_xlsx(response.content, titles=True)
            table = tariff_table(sheets, label)
            if self._raw_store is not None:
                self._raw_store.save(self.source.id, url, response.content, response.content_type or "application/octet-stream")
            self._tables[decision.doc_ids] = table
            return table
        raise UnexpectedResponse(f"EPDK tarife kararında okunabilir Excel tablosu yok ({label})")

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        needle = fold(query)
        rows = [
            (s.code, s.name, s.freq.value, None, None, s.remote)
            for s in self.source.series
            if not needle or needle in fold(f"{s.code} {s.name}")
        ]
        return pd.DataFrame(rows, columns=["series_id", "name", "freq", "start", "end", "datagroup"])

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        if request.formula or request.agg or request.freq not in (None, Frequency.MONTHLY):
            raise InvalidInput(
                f"{self.source.id}: formül, agregasyon ve frekans dönüşümü desteklenmiyor; dönüşümü yerelde yapın"
            )
        seeds = []
        for seed in map(self._seed, request.series_ids):
            section, groups, component = parse_remote(seed.remote or "")
            factor, fits = UNITS.get(seed.unit or "", (None, frozenset()))
            if factor is None or component not in fits:
                raise InvalidInput(f"{seed.code}: '{seed.unit}' birimi {component} bileşeni için kullanılamaz")
            seeds.append((seed, section, groups, component, factor))
        month_ends = _month_ends(request.period.start, min(request.period.end, self._today()))
        decisions = self.decisions() if month_ends else []
        vintage, rows = datetime.now(UTC), []
        for month_end in month_ends:
            decision = in_force(decisions, month_end)
            if decision is None:
                continue
            table = self._table(decision)
            for seed, section, groups, component, factor in seeds:
                if (value := table.get(section, groups, component)) is not None:
                    rows.append((seed.code, month_end, round(value * factor, 8)))
        if not rows:
            return empty_observations()
        frame = pd.DataFrame(rows, columns=["series_id", "date", "value"])
        by_code = {seed.code: seed for seed, *_ in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = "elektrik_tarife_tablolari"
        frame["entity_id"] = ""
        frame["entity_name"] = None
        frame["metric"] = metric_name(0)
        frame["freq"] = Frequency.MONTHLY.value
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c].unit)
        frame["currency"] = "TRY"
        frame["is_proxy"] = False
        frame["vintage_ts"] = vintage
        frame["publication_lag_days"] = frame["series_id"].map(self.source.lag_for)
        return frame.sort_values(["series_id", "date"], ignore_index=True)[OBSERVATION_COLUMNS]

    def healthcheck(self) -> HealthResult:
        if not self.source.canaries:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "katalogda canary seri yok")
        today, details, started = self._today(), [], time.monotonic()
        try:
            for seed in self.source.canaries:
                df = self.fetch(FetchRequest.create(self.source.id, [seed.code], today - timedelta(days=_HEALTH_LOOKBACK_DAYS), today))
                if df.empty:
                    return HealthResult(self.source.id, HealthStatus.FAIL, f"{seed.code}: son dönemde gözlem yok")
                last = df.sort_values("date").iloc[-1]
                if (today - last["date"]).days > Frequency.MONTHLY.max_staleness_days:
                    return HealthResult(self.source.id, HealthStatus.FAIL, f"{seed.code}: son gözlem {last['date']}")
                details.append(f"{seed.code} son={last['date']} değer={last['value']:g}")
            latest = max(self.decisions(), key=lambda d: (d.effective, d.decided))
            details.append(f"son karar {latest.number} ({latest.effective:%d.%m.%Y} itibarıyla)")
        except ProviderError as exc:
            return HealthResult(self.source.id, HealthStatus.FAIL, f"{type(exc).__name__}: {exc}")
        return HealthResult(
            self.source.id, HealthStatus.OK, "; ".join(details), (time.monotonic() - started) * 1000,
            tuple(s.code for s in self.source.canaries),
        )  # fmt: skip

    def close(self) -> None:
        self._http.close()
