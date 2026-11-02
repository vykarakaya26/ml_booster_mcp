"""TBB provider: quarterly bank-level financial statements from the TBB Veri Sorgulama Sistemi (keyless, anonymous).

Live findings 2026-11-02 (CLAUDE.md source rule 4):
- verisistemi.tbb.org.tr has no robots.txt (404). The terms of use of www.tbb.org.tr ("Kullanım Koşulları") allow
  publishing with attribution without permission; commercial use needs TBB's written permission.
- The server does not send its intermediate certificate (GlobalSign GCC R46 OV TLS CA 2025); browsers fetch it through
  the certificate's AIA URL. The intermediate is shipped in infrastructure/certs (see http_client._tls_context), so
  the chain is still verified up to a certifi root.
- The "Mali Tablolar" report page (index.php?/tbb/report_mali) fills its pivot from POST `/api/router` with a JSON body
  `{"route": ..., ...}` and only a `LANG` header (no token, cookie or login; a missing LANG answers 400). Routes used:
  `donemler` (periods: YIL, AY, KEY), `bankalar` (banks with data for the given period keys and items: BANKA_KODU,
  TR_ADI, GRUPLAR with entry/exit dates), `bankaGruplari` (group names) and `degerler` (values: one row per item,
  period and bank or group with TP_DEGER, YP_DEGER and TOPLAM in TL). An unknown route answers the text "asd".
- Items have numeric keys and belong to one statement set: "SOLO ... (12/2002-12/2017)" and "TFRS9-SOLO ...
  (3/2018-..)". A series therefore lists a chain of keys; for each bank and period the first key with a value is used.
- `bankalar` lists the banks that operate today, as the report page does: banks that closed earlier are part of the
  group totals but are not listed, so early periods have an unassigned remainder (about 9 % of 2002 assets).
- The group memberships in `bankalar` are incomplete (ING Bank has no group after 2007-10, Fibabanka none after 2002),
  so banks cannot be assigned to sub-groups reliably: only the system group (5) is offered, its banks are all banks.
- Participation banks are not TBB members; group 5 "Türkiye Bankacılık Sistemi" covers deposit banks and development
  and investment banks. Bank-level values add up to the group totals (≈ 0.01 % difference in 2026).
- Raw responses go to the bronze store, whose content hash serves as change detection.

The catalog `remote` field is `<group>/<key>[+<older key>...][.<column>]`, e.g. `5/16018+2087`: the group total is
written with an empty `entity_id`, the banks are entities (entity_id = TBB bank code). The group must be 5. Columns are
TOPLAM (default), TP and YP. Requests go only to the host pinned below.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, date, datetime, timedelta
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

HOST = "verisistemi.tbb.org.tr"
ROUTER_PATH = "/api/router"
SYSTEM_GROUP = 5  # "Türkiye Bankacılık Sistemi": every bank in TBB's statistics
PERIODS_PER_CALL = 20
TL_PER_UNIT = 1_000_000  # the API answers in TL; series are kept in million TL like BDDK
_COLUMNS = {"TOPLAM": "TOPLAM", "TP": "TP_DEGER", "YP": "YP_DEGER"}
_HEALTH_LOOKBACK_DAYS = 400
_STALENESS_MARGIN_DAYS = 30  # December statements come out at the end of April (about 120 days)

_REMOTE = re.compile(r"^(?P<group>[0-9]{1,2})/(?P<keys>[0-9]{1,9}(?:\+[0-9]{1,9}){0,5})(?:\.(?P<column>TOPLAM|TP|YP))?$")
_TR_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "IIiSsGgUuOoCc")


def parse_remote(remote: str) -> tuple[int, tuple[int, ...], str]:
    """(group, item keys newest first, value field) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    if m is None:
        raise InvalidInput(f"TBB remote alanı '<grup>/<kalem>[+<eski kalem>...][.<kolon>]' biçiminde olmalıdır: {remote!r}")
    if int(m["group"]) != SYSTEM_GROUP:
        raise InvalidInput(f"TBB remote alanında yalnızca {SYSTEM_GROUP} numaralı sistem grubu desteklenir: {remote!r}")
    keys = tuple(int(k) for k in m["keys"].split("+"))
    if len(set(keys)) != len(keys):
        raise InvalidInput(f"TBB remote alanında tekrarlanan kalem var: {remote!r}")
    return int(m["group"]), keys, _COLUMNS[m["column"] or "TOPLAM"]


def _quarter_end(year: int, month: int) -> date:
    return date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)


def _banks(rows: list[dict]) -> dict[int, str]:
    """{bank code: name} of the `bankalar` answer (addresses and other fields are not kept)."""
    try:
        return {int(r["BANKA_KODU"]): " ".join(str(r["TR_ADI"]).split()) for r in rows}
    except (KeyError, TypeError, ValueError):
        raise UnexpectedResponse("TBB banka listesinin yapısı değişmiş") from None


class TbbProvider:
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
        self._url = f"https://{HOST}{ROUTER_PATH}"
        self._http = http
        self._raw_store = raw_store

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is None or not seed.remote:
            known = ", ".join(s.code for s in self.source.series)
            raise InvalidInput(f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}")
        return seed

    def _call(self, body: dict) -> list[dict]:
        response = self._http.post_json(self._url, body, headers={"Accept": "application/json", "LANG": "tr"})
        rows = self._json(response, str(body["route"]))
        if self._raw_store is not None:  # the manifest URL names the (public) request body
            query = json.dumps(body, separators=(",", ":"), sort_keys=True)
            self._raw_store.save(self.source.id, f"{self._url}#{query}", response.content, "application/json")
        return rows

    def _json(self, response: HttpResponse, route: str) -> list[dict]:
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"TBB {response.status} yönlendirmesi döndürdü: {response.url}")
        if response.status != 200:
            raise ProviderError(f"TBB HTTP {response.status} ({route}): {response.url}")
        if "json" not in response.content_type or response.content.lstrip()[:1] == b"<":
            raise UnexpectedResponse(f"TBB JSON yerine başka bir içerik döndürdü ({response.content_type[:40]}): {route}")
        try:
            doc = json.loads(response.content)
        except ValueError:
            raise UnexpectedResponse(f"TBB yanıtı çözümlenemedi ({route}): {response.text(40)!r}") from None
        if not isinstance(doc, list) or not all(isinstance(r, dict) for r in doc):
            raise UnexpectedResponse(f"TBB yanıtı beklenen liste biçiminde değil ({route})")
        return doc

    def _periods(self, start: date, end: date) -> list[tuple[date, int]]:
        """(period end, period key) of the published quarters in [start, end], oldest first."""
        out = []
        for row in self._call({"route": "donemler"}):
            try:
                day, key = _quarter_end(int(row["YIL"]), int(row["AY"])), int(row["KEY"])
            except (KeyError, TypeError, ValueError):
                raise UnexpectedResponse("TBB dönem listesinin yapısı değişmiş") from None
            if start <= day <= end:
                out.append((day, key))
        return sorted(out)

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
        periods = self._periods(request.period.start, request.period.end)
        if not periods:
            return empty_observations()
        items = sorted({k for _, (_, keys, _) in seeds for k in keys})
        groups = sorted({g for _, (g, _, _) in seeds})
        period_keys = [key for _, key in periods]
        banks = _banks(self._call({"route": "bankalar", "donemler": period_keys, "maliTablolar": items}))
        group_names = self._group_names(period_keys, items)
        values: dict[tuple[bool, int, int, int], dict] = {}  # (is group, code, item, period key) → row
        for i in range(0, len(period_keys), PERIODS_PER_CALL):
            body = {"route": "degerler", "donemler": period_keys[i : i + PERIODS_PER_CALL], "maliTablolar": items,
                    "bankalar": sorted(banks), "bankaGruplari": groups}  # fmt: skip
            for row in self._call(body):
                try:
                    values[(row["BANKA_GRUP"] is True, int(row["BANKA_KODU"]), int(row["UNIQUE_KEY"]), int(row["DONEM_KEY"]))] = row
                except (KeyError, TypeError, ValueError):
                    raise UnexpectedResponse("TBB değer satırlarının yapısı değişmiş") from None
        rows = []
        for seed, (group, keys, column) in seeds:
            for day, period in periods:
                total = _first(values, True, group, keys, period, column)
                if total is not None:
                    rows.append((seed.code, "", group_names.get(group), day, total))
                for code, name in banks.items():
                    value = _first(values, False, code, keys, period, column)
                    if value is not None:
                        rows.append((seed.code, str(code), name, day, value))
        frame = pd.DataFrame(rows, columns=["series_id", "entity_id", "entity_name", "date", "value"])
        if frame.empty:
            return empty_observations()
        by_code = {seed.code: seed for seed, _ in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = "mali_tablolar_solo"
        frame["metric"] = metric_name(0)
        frame["freq"] = frame["series_id"].map(lambda c: by_code[c].freq.value)
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c].unit)
        frame["currency"] = frame["series_id"].map(lambda c: by_code[c].currency)
        frame["is_proxy"] = False  # bank-level statements of every TBB member bank
        frame["vintage_ts"] = datetime.now(UTC)
        frame["publication_lag_days"] = frame["series_id"].map(self.source.lag_for)
        return frame.sort_values(["series_id", "entity_id", "date"], ignore_index=True)[OBSERVATION_COLUMNS]

    def _group_names(self, period_keys: list[int], items: list[int]) -> dict[int, str]:
        names = {}
        for row in self._call({"route": "bankaGruplari", "donemler": period_keys, "maliTablolar": items}):
            try:
                names[int(row["GRUP_NO"])] = " ".join(str(row["TR_ADI"]).split())
            except (KeyError, TypeError, ValueError):
                raise UnexpectedResponse("TBB banka grubu listesinin yapısı değişmiş") from None
        return names

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
                banks = int((df[df["date"] == last["date"]]["entity_id"] != "").sum())
                details.append(f"{seed.code} son={last['date']} değer={last['value']:g} banka={banks}")
        except ProviderError as exc:
            return HealthResult(self.source.id, HealthStatus.FAIL, f"{type(exc).__name__}: {exc}")
        return HealthResult(
            self.source.id, HealthStatus.OK, "; ".join(details), (time.monotonic() - started) * 1000,
            tuple(s.code for s in self.source.canaries),
        )  # fmt: skip

    def close(self) -> None:
        self._http.close()


def _first(values: dict, is_group: bool, code: int, keys: tuple[int, ...], period: int, column: str) -> float | None:
    """Value of the first item key that has one for this bank or group and period (statement sets change over time)."""
    for key in keys:
        row = values.get((is_group, code, key, period))
        if row is None:
            continue
        raw = row.get(column)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise UnexpectedResponse(f"TBB yanıtında sayısal olmayan değer: {str(raw)[:20]!r}")
        return float(raw) / TL_PER_UNIT
    return None
