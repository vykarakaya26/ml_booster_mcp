"""MCP sunucusu (stdio). Claude Code'a ekleme:

    claude mcp add mlbooster -e EVDS_API_KEY=... -- mlbooster-mcp

Güvenlik: araç girdileri use case kapısında izin listesiyle doğrulanır; beklenen hatalar güvenli
mesajlarla `ToolError` olarak döner, beklenmeyen hataların içeriği istemciye hiç gönderilmez
(MCP SDK). Dosya yolu alan araç yoktur; çıktılar yalnızca yerel veri dizinine yazılır.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from datetime import date
from functools import cache
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from mlbooster.application.dto import FetchRequest
from mlbooster.application.use_cases.panel import DEFAULT_START
from mlbooster.bootstrap import Container, build_container
from mlbooster.domain.errors import DomainError, InvalidInput
from mlbooster.domain.services.backtest import MODELS, BacktestConfig
from mlbooster.interfaces.presenters import backtest_dict, health_dict, quality_dict, sector_map_markdown, source_dict

log = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
READ_REMOTE = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITES_LOCAL = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

server = MCPServer(
    "mlbooster",
    instructions=(
        "Türkiye için ücretsiz veri kaynakları haritası ve ML'e hazır aylık panel üretici. "
        "Önce list_sectors / show_map ile kaynakları gör; EVDS serilerini discover_series ile keşfet; "
        "fetch_series ile çek; build_panel ile sektör panelini üret. Verilerde kaynak atfı zorunludur."
    ),
)


@cache
def container() -> Container:
    return build_container()


def _iso(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise InvalidInput(f"{field} YYYY-AA-GG biçiminde olmalı") from None


def safe_tool(fn: F) -> F:
    """Beklenen (domain/port) hataları kullanıcıya gösterilebilir ToolError'a çevirir."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DomainError as exc:
            raise ToolError(str(exc)) from None

    return wrapper  # type: ignore[return-value]


@server.tool(annotations=READ_ONLY)
@safe_tool
def list_sectors() -> list[dict]:
    """Tanımlı sektörleri (id, ad) listeler."""
    return [{"id": s.id, "name": s.name} for s in container().list_sectors()]


@server.tool(annotations=READ_ONLY)
@safe_tool
def list_sources(sector: str | None = None) -> list[dict]:
    """Kaynak kataloğunu listeler; sector verilirse o sektörle ilgili kaynaklar."""
    ready = container().providers.available()
    return [source_dict(s, s.provider in ready) for s in container().list_sources(sector)]


@server.tool(annotations=READ_ONLY)
@safe_tool
def show_map(sector: str) -> str:
    """Bir sektörün veri kaynağı haritasını (kaynaklar, makro feature'lar, pazar payı) markdown döner."""
    return sector_map_markdown(container().sector_map(sector))


@server.tool(annotations=READ_REMOTE)
@safe_tool
def discover_series(datagroup: str | None = None, source: str = "evds3") -> list[dict]:
    """Seri keşfi. EVDS veri grubu kodu (ör. bie_dkdovizgn) verilirse canlı /serieList sonucu,
    verilmezse katalogdaki tohum seriler döner."""
    return container().discover_series(source, datagroup).head(1000).to_dict(orient="records")


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def fetch_series(
    series: list[str],
    start: str = DEFAULT_START.isoformat(),
    end: str | None = None,
    source: str = "evds3",
    freq: str | None = None,
    agg: str | None = None,
    formula: int = 0,
    max_rows: int = 50,
) -> dict:
    """Serileri çeker ve yerel depoya yazar. Tarihler YYYY-AA-GG.
    freq: D,B,W,SM,M,Q,S,A; agg: avg,min,max,first,last,sum; formula: 0 düzey, 1 %değişim, 3 yıllık %değişim.
    Özet ve son `max_rows` (≤500) satırı döner."""
    request = FetchRequest.create(
        source, series, _iso(start, "start"), _iso(end, "end") if end else None, freq, agg, formula
    )
    s = container().fetch_series.summarize(request, max_rows)
    return {"rows": s.rows, "summary": s.by_series, "tail": s.tail, "attribution": s.attribution}


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def build_panel(sector: str, start: str = DEFAULT_START.isoformat(), end: str | None = None) -> dict:
    """Sektörün makro feature'larından aylık panel üretir ve yerel veri dizinine Parquet olarak yazar."""
    result = container().build_panel(sector, _iso(start, "start"), _iso(end, "end") if end else None)
    tail = result.panel.tail(6).round(4).reset_index()
    return {
        "path": result.location,
        "months": int(result.panel.shape[0]),
        "features": list(result.panel.columns),
        "skipped": result.skipped,
        "tail": tail.astype({"date": str}).to_dict(orient="records") if not tail.empty else [],
    }


@server.tool(annotations=READ_REMOTE)
@safe_tool
def health(source: str | None = None) -> list[dict]:
    """Kanarya testleri: sağlayıcıların kritik serilerden son gözlemleri çekip çekemediği."""
    return [health_dict(r) for r in container().health(source)]


@server.tool(annotations=READ_REMOTE)
@safe_tool
def search_series(text: str, source: str = "evds3", limit: int = 20) -> list[dict]:
    """Kaynakta veri grubu arar (Türkçe karakter duyarsız, ör. 'kredi faiz', 'tüketici güven').
    Dönen `datagroup` ile discover_series çağrılarak içindeki seri kodları listelenir. Arşiv grupları sondadır."""
    return container().search_series(text, source, limit).to_dict(orient="records")


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def set_feature_series(
    sector: str,
    feature: str,
    series: str,
    name: str | None = None,
    kind: str = "level",
    publication_lag_days: int | None = None,
    datagroup: str | None = None,
) -> dict:
    """Keşif bekleyen bir sektör feature'ına seri kodu bağlar. Kod önce canlı doğrulanır (son 2 yılda veri,
    seri durmamış). Kullanıcının yerel override dosyasına yazar; paket kataloğu değişmez."""
    b = container().set_feature_series(sector, feature, series, name, kind, publication_lag_days, datagroup)
    return {
        "sector": b.sector_id, "feature": b.feature, "series": b.seed.code, "freq": b.seed.freq.value,
        "publication_lag_days": b.seed.publication_lag_days, "last_observation": b.last_observation.isoformat(),
    }  # fmt: skip


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def audit_panel(sector: str, start: str = DEFAULT_START.isoformat(), end: str | None = None) -> dict:
    """Sektör panelinin veri kalitesini denetler: bayat/durmuş seri, eksik oranı, aykırı değer (robust z),
    yapısal kırılma (bilinen TR şoklarıyla eşleştirilmiş), aşırı korelasyon."""
    report, result = container().audit_panel(sector, _iso(start, "start"), _iso(end, "end") if end else None)
    return quality_dict(report, result.skipped)


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def run_backtest(
    sector: str,
    target: str,
    horizon: int = 1,
    min_train: int = 48,
    models: list[str] | None = None,
    exogenous: bool = True,
    anchor: str | None = None,
    start: str = DEFAULT_START.isoformat(),
    end: str | None = None,
) -> dict:
    """Panel hedef kolonu için rolling-origin backtest (look-ahead korumalı). Modeller: naive, seasonal_naive,
    drift, ridge (gecikmeli; exogenous=True ise diğer feature'larla). Metrikler: MAE, RMSE, sMAPE, MASE.
    anchor: hedefin daha güncel versiyonu (ör. aofm_son); verilirse anchor_naive modeli eklenir — aylık
    ortalama hedeflerde gerçek çıta budur. Son 24 tahmin noktası döner."""
    config = BacktestConfig(
        target=target, horizon=horizon, min_train=min_train, models=tuple(models or MODELS), exogenous=exogenous,
        anchor=anchor,
    )
    result, _ = container().run_backtest(sector, config, _iso(start, "start"), _iso(end, "end") if end else None)
    return backtest_dict(result, sector, max_predictions=24)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
