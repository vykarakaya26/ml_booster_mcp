"""BDDK provider: monthly banking sector data from the public Aylık Bülten (keyless, anonymous).

Live findings 2026-11-02 (CLAUDE.md source rule 4):
- www.bddk.org.tr has no robots.txt (404). The terms of use allow partial quotation with attribution; commercial use
  needs BDDK's written permission (recorded in the catalog licence note).
- The bulletin page (/BultenAylik) fills its HTML table from a JSON endpoint: POST form-urlencoded to
  `/BultenAylik/tr/Home/BasitRaporGetir` with `tabloNo`, `yil`, `ay`, `paraBirimi` and one `taraf` per bank group.
  One call returns one month for every requested group; rows are `[group label, order, row label, font, values...]`
  and the value columns are named in `colModels` (e.g. `Tp`, `Yp`, `Toplam`). Values are in million TL.
- Groups come back sorted by their `taraf` code, whatever the request order. Ownership groups (Kamu, Yerli Özel,
  Yabancı) and function groups (Mevduat-Kamu/-Yerli Özel/-Yabancı, Katılım, Kalkınma ve Yatırım) each add up to
  the sector (Sektör, 10001) up to rounding.
- Months that are not published answer `{"success": false, "error": "..."}` with a Turkish message that names the
  available range ("2026 yılının en son 9 ayına ait veri bulunmaktadır!"); the series start in 2002.
- Row labels change over time (e.g. "TOPLAM AKTİFLER (1+..+20)-(12+13)" in 2002 vs "TOPLAM AKTİFLER" today), so
  rows are matched by a normalised label key instead of their position.
- The same report is offered as an Excel download (BasitExceleAktar, GET); the JSON carries identical values and is
  used instead. Raw responses go to the bronze store, whose content hash serves as change detection.

The catalog `remote` field is `<table>[,<total group>+<group>+...]/<ROW_KEY>[.<column>]`, e.g.
`1,10001+10009+10008+10010+10003+10004/TOPLAM_AKTIFLER`: the first group is the total (written with an empty
`entity_id`), the others are entities (entity_id = BDDK group code). Without groups the sector total (10001) is used;
without a column the `Toplam` column is read. Requests go only to the host pinned below.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

HOST = "www.bddk.org.tr"
REPORT_PATH = "/BultenAylik/tr/Home/BasitRaporGetir"
SECTOR_GROUP = "10001"
DEFAULT_COLUMN = "Toplam"
FIRST_MONTH = date(2002, 1, 1)  # "Seçili tablo için veriler 2002 yılı, 1. aydan başlamaktadır."
_HEALTH_LOOKBACK_DAYS = 120
_STALENESS_MARGIN_DAYS = 15

_REMOTE = re.compile(r"^(?P<table>[0-9]{1,2})(?:,(?P<groups>[0-9]{5}(?:\+[0-9]{5}){0,11}))?/(?P<row>[A-Z0-9_]{1,80})(?:\.(?P<column>[A-Za-z]{1,30}))?$")  # fmt: skip
_TR_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "IIiSsGgUuOoCc")
# Messages for months without data; they also tell which months of the year exist.
_LATEST = re.compile(r"(\d{4}) yılının en son (\d{1,2}) ayına ait veri bulunmaktadır")
_ONLY = re.compile(r"(\d{4}) yılının sadece (\d{1,2})\. ayına ait veri bulunmaktadır")
_NO_YEAR = re.compile(r"(\d{4}) yılına ait veri bulunmamaktadır")
_STARTS = re.compile(r"veriler (\d{4}) yılı, (\d{1,2})\. aydan başlamaktadır")


def row_key(label: str) -> str:
    """Stable key of a bulletin row label: ASCII upper case, without footnote stars and parenthesised parts."""
    text = re.sub(r"\([^)]*\)", " ", label.translate(_TR_FOLD)).replace("*", " ")
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")


def parse_remote(remote: str) -> tuple[int, tuple[str, ...], str, str]:
    """(table, groups with the total first, row key, column) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    if m is None:
        raise InvalidInput(f"BDDK remote alanı '<tablo>[,<toplam grup>+<grup>...]/<SATIR>[.<kolon>]' biçiminde olmalıdır: {remote!r}")
    groups = tuple(m["groups"].split("+")) if m["groups"] else (SECTOR_GROUP,)
    if len(set(groups)) != len(groups):
        raise InvalidInput(f"BDDK remote alanında tekrarlanan grup var: {remote!r}")
    return int(m["table"]), groups, m["row"], m["column"] or DEFAULT_COLUMN


def _months(start: date, end: date) -> list[tuple[int, int]]:
    y, m = max(start, FIRST_MONTH).year, max(start, FIRST_MONTH).month
    out = []
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _month_end(year: int, month: int) -> date:
    return (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)


class _Availability:
    """Months the bulletin says it has, learned from the 'no data' messages (saves one request per missing month)."""

    def __init__(self) -> None:
        self.years: dict[int, set[int]] = {}
        self.first: tuple[int, int] | None = None

    def learn(self, message: str) -> bool:
        if m := _LATEST.search(message):
            self.years[int(m[1])] = set(range(1, int(m[2]) + 1))
        elif m := _ONLY.search(message):
            self.years[int(m[1])] = {int(m[2])}
        elif m := _NO_YEAR.search(message):
            self.years[int(m[1])] = set()
        elif m := _STARTS.search(message):
            self.first = (int(m[1]), int(m[2]))
        else:
            return False
        return True

    def has(self, year: int, month: int) -> bool:
        if self.first is not None and (year, month) < self.first:
            return False
        return month in self.years.get(year, range(1, 13))


class BddkProvider:
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
        self._url = f"https://{HOST}{REPORT_PATH}"
        self._http = http
        self._raw_store = raw_store

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is None or not seed.remote:
            known = ", ".join(s.code for s in self.source.series)
            raise InvalidInput(f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}")
        return seed

    def _report(self, table: int, groups: list[str], year: int, month: int) -> dict | str:
        """`Json` part of the bulletin report, or the bulletin's message if the month has no data."""
        form = [("tabloNo", str(table)), ("yil", str(year)), ("ay", str(month)), ("paraBirimi", "TL")]
        form += [("taraf", g) for g in groups]
        response = self._http.post_form(self._url, form, headers={"Accept": "application/json"})
        doc = self._json(response)
        if doc.get("success") is not True:
            message = str(doc.get("error") or "")[:200]
            return message or "(boş mesaj)"
        body = doc.get("Json")
        if not isinstance(body, dict):
            raise UnexpectedResponse(f"BDDK yanıtında rapor gövdesi yok ({table}. tablo, {year}/{month})")
        if self._raw_store is not None:  # the URL in the manifest carries the (public) form fields
            self._raw_store.save(self.source.id, f"{self._url}?{urlencode(form)}", response.content, "application/json")
        return body

    def _json(self, response: HttpResponse) -> dict:
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"BDDK {response.status} yönlendirmesi döndürdü: {response.url}")
        if response.status != 200:
            raise ProviderError(f"BDDK HTTP {response.status}: {response.url}")
        if "json" not in response.content_type or response.content.lstrip()[:1] == b"<":
            raise UnexpectedResponse(
                f"BDDK JSON yerine başka bir içerik döndürdü ({response.content_type[:40]}): {response.url}"
            )
        try:
            doc = json.loads(response.content)
        except ValueError:
            raise UnexpectedResponse(f"BDDK yanıtı çözümlenemedi: {response.url}") from None
        if not isinstance(doc, dict):
            raise UnexpectedResponse(f"BDDK yanıtı beklenen nesne biçiminde değil: {response.url}")
        return doc

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
        tables: dict[int, set[str]] = {}
        for _, (table, groups, _, _) in seeds:
            tables.setdefault(table, set()).update(groups)
        vintage, rows = datetime.now(UTC), []
        for table, group_set in sorted(tables.items()):
            groups = sorted(group_set)  # the bulletin returns groups in code order
            available = _Availability()
            for year, month in _months(request.period.start, request.period.end):
                if not available.has(year, month):
                    continue
                report = self._report(table, groups, year, month)
                if isinstance(report, str):
                    if not available.learn(report):
                        raise UnexpectedResponse(f"BDDK raporu üretilemedi ({table}. tablo, {year}/{month}): {report}")
                    continue
                cells = _cells(report, groups)
                for seed, (t, wanted, row, column) in seeds:
                    if t == table:
                        rows += _pick(cells, seed, wanted, row, column, _month_end(year, month))
        frame = pd.DataFrame(rows, columns=["series_id", "entity_id", "entity_name", "date", "value"])
        if frame.empty:
            return empty_observations()
        by_code = {seed.code: seed for seed, _ in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = frame["series_id"].map(lambda c: f"aylik_bulten_{parse_remote(by_code[c].remote or '')[0]}")
        frame["metric"] = metric_name(0)
        frame["freq"] = frame["series_id"].map(lambda c: by_code[c].freq.value)
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c].unit)
        frame["currency"] = frame["series_id"].map(lambda c: by_code[c].currency)
        frame["is_proxy"] = False  # the bulletin covers every bank in the sector
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


def _cells(report: dict, groups: list[str]) -> dict[str, tuple[str, dict[str, dict[str, object]]]]:
    """{group code: (group label, {row key: {column: value}})} of one monthly report."""
    try:
        names = [c["name"] for c in report["colModels"]]
        raw_rows = report["data"]["rows"]
        cells = [r["cell"] for r in raw_rows]
    except (KeyError, TypeError):
        raise UnexpectedResponse("BDDK raporunda kolon ya da satır listesi yok") from None
    if not {"BankaAdi", "Ad"} <= set(names) or not all(isinstance(c, list) and len(c) == len(names) for c in cells):
        raise UnexpectedResponse("BDDK raporunun kolon yapısı değişmiş")
    g, a = names.index("BankaAdi"), names.index("Ad")
    labels: list[str] = []
    for c in cells:
        if c[g] not in labels:
            labels.append(c[g])
    if len(labels) != len(groups):
        raise UnexpectedResponse(f"BDDK raporunda {len(groups)} grup istendi, {len(labels)} grup döndü")
    code_of = dict(zip(labels, groups, strict=True))
    out: dict[str, tuple[str, dict[str, dict[str, object]]]] = {code: (str(label), {}) for label, code in code_of.items()}
    for c in cells:
        out[code_of[c[g]]][1].setdefault(row_key(str(c[a])), dict(zip(names, c, strict=True)))
    return out


def _pick(cells, seed: SeedSeries, groups, row: str, column: str, period_end: date) -> list[tuple]:
    rows = []
    for i, code in enumerate(groups):
        label, table = cells[code]
        if row not in table:
            raise UnexpectedResponse(f"{seed.code}: BDDK raporunda '{row}' satırı bulunamadı ({period_end:%Y-%m})")
        if column not in table[row]:
            raise UnexpectedResponse(f"{seed.code}: BDDK raporunda '{column}' kolonu yok")
        raw = table[row][column]
        if raw is None or raw == "":
            continue
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise UnexpectedResponse(f"BDDK yanıtında sayısal olmayan değer: {str(raw)[:20]!r}")
        rows.append((seed.code, "" if i == 0 else code, label, period_end, float(raw)))
    return rows
