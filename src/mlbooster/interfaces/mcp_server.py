"""MCP server (stdio). Adding it to Claude Code:

    claude mcp add mlbooster -e EVDS_API_KEY=... -- mlbooster-mcp

Security: tool inputs are validated against allow-lists at the use case gate; expected errors are
returned as `ToolError` with safe messages, and the content of unexpected errors is never sent to the
client (MCP SDK). No tool takes a file path; outputs are written only to the local data directory.

Server instructions and tool descriptions are user-facing and therefore written in Turkish.
"""

from __future__ import annotations

import functools
import logging
import sys
from collections.abc import Callable
from datetime import date
from functools import cache
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from mlbooster.application.dto import FetchRequest
from mlbooster.application.use_cases.panel import DEFAULT_START
from mlbooster.bootstrap import Container, build_container
from mlbooster.domain.errors import DomainError, InvalidInput
from mlbooster.domain.services.backtest import MODELS, BacktestConfig
from mlbooster.domain.services.feature_engineering import FeatureConfig
from mlbooster.domain.services.market_share import ShareConfig
from mlbooster.domain.services.preprocessing import PrepConfig
from mlbooster.interfaces.presenters import (
    backtest_dict,
    features_dict,
    health_dict,
    prep_dict,
    quality_dict,
    sector_map_markdown,
    source_dict,
)

log = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
READ_REMOTE = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITES_LOCAL = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

from mlbooster import __version__  # noqa: E402

server = MCPServer(
    "mlbooster",
    version=__version__,
    instructions=(
        "Türkiye için ücretsiz veri kaynaklarını haritalar ve ML'e hazır aylık panel üretir. "
        "Önce list_sectors / show_map ile kaynakları incele; EVDS serilerini search_series ve discover_series ile "
        "bul, fetch_series ile çek, build_panel ile sektör panelini üret. Verileri kullanırken kaynak atfı "
        "zorunludur. Oturum başında check_updates ile yeni sürüm olup olmadığını kontrol et ve varsa kullanıcıya "
        "bir kez bildir."
    ),
)


@cache
def container() -> Container:
    return build_container()


def _iso(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise InvalidInput(f"{field} YYYY-AA-GG biçiminde olmalıdır") from None


def safe_tool[F: Callable[..., Any]](fn: F) -> F:
    """Converts expected (domain/port) errors into a ToolError that can be shown to the user."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DomainError as exc:
            raise ToolError(str(exc)) from None
        except Exception as exc:
            # The SDK hides unexpected errors from the client; leave a trace on stderr (stdout is the stdio
            # transport). Only the type is logged: messages and tracebacks may carry paths or request details.
            log.error("tool %s failed unexpectedly: %s", fn.__name__, type(exc).__name__)
            raise

    return wrapper  # type: ignore[return-value]


@server.tool(annotations=READ_ONLY)
@safe_tool
def list_sectors() -> list[dict]:
    """Tanımlı sektörleri (id, ad) listeler."""
    return [{"id": s.id, "name": s.name} for s in container().list_sectors()]


@server.tool(annotations=READ_ONLY)
@safe_tool
def list_sources(sector: str | None = None) -> list[dict]:
    """Kaynak kataloğunu listeler; sector verilirse yalnızca o sektörle ilgili kaynakları döner."""
    ready = container().ready_providers()
    return [source_dict(s, s.provider in ready) for s in container().list_sources(sector)]


@server.tool(annotations=READ_ONLY)
@safe_tool
def show_map(sector: str) -> str:
    """Bir sektörün veri kaynağı haritasını (kaynaklar, makro feature'lar, pazar payı) markdown olarak döner."""
    return sector_map_markdown(container().sector_map(sector))


@server.tool(annotations=READ_REMOTE)
@safe_tool
def discover_series(datagroup: str | None = None, source: str = "evds3") -> list[dict]:
    """Seri keşfi. EVDS veri grubu kodu (ör. bie_dkdovizgn) verilirse canlı /serieList sonucunu,
    verilmezse katalogdaki seed serileri döner."""
    return container().discover_series(source, datagroup).head(1000).to_dict(orient="records")


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def fetch_series(
    series: list[str],
    start: str | None = None,
    end: str | None = None,
    source: str = "evds3",
    freq: str | None = None,
    agg: str | None = None,
    formula: int = 0,
    max_rows: int = 50,
    refresh: bool = False,
) -> dict:
    """Serileri çeker ve yerel depoya yazar. Tarihler YYYY-AA-GG biçimindedir; start verilmezse kaynağın varsayılan
    geçmişi kullanılır (EPİAŞ: son 5 yıl, diğerleri 2010-01-01). Kullanıcı farklı bir geçmiş isterse (ör. 2 yıl)
    start ile verin. EPİAŞ gibi artımlı kaynaklarda yerel depodaki dönemler yeniden çekilmez; yalnızca eksik
    geçmiş ve son günler istenir (refresh=true bütün aralığı yeniden çeker), `fetched` kaynaktan istenen dönemleri
    gösterir. freq: D,B,W,SM,M,Q,S,A; agg: avg,min,max,first,last,sum; formula: 0 düzey, 1 yüzde değişim,
    3 yıllık yüzde değişim. Özeti ve son `max_rows` (≤500) satırı döner.
    Google Trends (source=bq_trends): Türkiye'nin günlük Top 25 listeleri BigQuery'den çekilir; kaynak yalnızca son
    30 günü tuttuğu için geçmiş yerel depoda birikir. BQT.ILGI.<TERIM> (ör. BQT.ILGI.TOGG) listeye girmiş bir terimin
    5 yıllık haftalık arama ilgisini verir. BQ_PROJECT_ID ve Google ADC (`gcloud auth application-default login`)
    gerekir; değerler arama ilgisi proxy'sidir, pazar payı değildir."""
    fetcher = container().fetch_series
    first = _iso(start, "start") if start else fetcher.default_start(source, DEFAULT_START)
    request = FetchRequest.create(source, series, first, _iso(end, "end") if end else None, freq, agg, formula)
    s = fetcher.summarize(request, max_rows, refresh=refresh)
    return {"rows": s.rows, "summary": s.by_series, "tail": s.tail, "attribution": s.attribution, "fetched": s.fetched}


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def build_panel(sector: str, start: str = DEFAULT_START.isoformat(), end: str | None = None, freq: str = "M") -> dict:
    """Sektörün feature'larından point-in-time panel üretir (freq: M aylık, W haftalık/Cuma, B iş günü) ve
    yerel veri dizinine Parquet olarak yazar. Kalite denetimi ve backtest aylık panel üzerinde çalışır."""
    result = container().build_panel(sector, _iso(start, "start"), _iso(end, "end") if end else None, freq=freq)
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
    """Healthcheck (canary): provider'ların kritik serilerin son gözlemlerini çekebildiğini doğrular."""
    return [health_dict(r) for r in container().health(source)]


@server.tool(annotations=READ_REMOTE)
@safe_tool
def search_series(text: str, source: str = "evds3", limit: int = 20) -> list[dict]:
    """Kaynakta veri grubu arar (Türkçe karakter farkı gözetilmez; ör. 'kredi faiz', 'tüketici güven').
    Dönen `datagroup` ile discover_series çağrılarak gruptaki seri kodları listelenir. Arşiv grupları en sonda yer alır."""
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
    create: bool = False,
    transform: str = "level",
    agg: str | None = None,
) -> dict:
    """Bir sektör feature'ına seri kodu bağlar; create=True ise yeni feature ekler (transform: level, diff,
    log_diff, yoy_pct; agg: avg, last, sum). Kod önce canlı doğrulanır: son 2 yılda gözlemi bulunmalı ve seri stale
    olmamalıdır. Sonuç kullanıcının yerel override dosyasına yazılır; paket kataloğu değişmez."""
    b = container().set_feature_series(
        sector, feature, series, name, kind, publication_lag_days, datagroup,
        create=create, transform=transform, agg=agg,
    )  # fmt: skip
    return {
        "sector": b.sector_id, "feature": b.feature, "series": b.seed.code, "freq": b.seed.freq.value,
        "publication_lag_days": b.seed.publication_lag_days, "last_observation": b.last_observation.isoformat(),
    }  # fmt: skip


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def audit_panel(sector: str, start: str = DEFAULT_START.isoformat(), end: str | None = None) -> dict:
    """Sektör panelinin veri kalitesini denetler: stale seri, eksik gözlem oranı, outlier (robust z-score),
    structural break (Türkiye'deki bilinen şoklarla eşleştirilir) ve aşırı korelasyon."""
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
    """Paneldeki hedef kolon için rolling-origin backtest (look-ahead korumalı). Modeller: naive, seasonal_naive,
    drift, ridge (lag'li; exogenous=True ise diğer feature'larla birlikte). Metrikler: MAE, RMSE, sMAPE, MASE.
    anchor: hedefin daha güncel bir versiyonu (ör. aofm_son); verilirse anchor_naive modeli eklenir. Aylık
    ortalama hedeflerde asıl benchmark budur. Son 24 forecast noktasını döner."""
    config = BacktestConfig(
        target=target, horizon=horizon, min_train=min_train, models=tuple(models or MODELS), exogenous=exogenous,
        anchor=anchor,
    )
    result, _ = container().run_backtest(sector, config, _iso(start, "start"), _iso(end, "end") if end else None)
    return backtest_dict(result, sector, max_predictions=24)


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def prepare_panel(
    sector: str,
    target: str | None = None,
    train_end: str | None = None,
    model_family: str = "linear",
    start: str = DEFAULT_START.isoformat(),
) -> dict:
    """Dağılıma dayalı ön işleme planı. Her feature için eğitim penceresindeki profili (ortalama/std, medyan/MAD/IQR,
    çarpıklık, basıklık, outlier oranı, durağanlık) ve seçilen adımları (imputation, fark alma, outlier winsorize/
    işaretleme, log/Yeo–Johnson, standard/robust scaling) gerekçeleriyle döner. train_end: YYYY-AA-GG; parametreler
    yalnızca bu tarihe kadarki veriden tahmin edilir. Hedef kolonun dağılımı değiştirilmez."""
    config = PrepConfig(target=target, train_end=_iso(train_end, "train_end") if train_end else None, model_family=model_family)
    result, panel = container().prepare_panel(sector, config, _iso(start, "start"))
    return prep_dict(result, sector, panel.skipped)


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def engineer_features(
    sector: str,
    target: str | None = None,
    horizon: int = 1,
    train_end: str | None = None,
    prep: bool = True,
    start: str = DEFAULT_START.isoformat(),
) -> dict:
    """Sektöre özgü otomatik feature üretimi: lag'ler, rolling mean/rolling std, momentum, z-score, EWM, sektör
    tarifleri (reel faiz, faiz farkı, kur sepeti…), takvim (iş günü, bayram) ve olay göstergeleri. target verilirse
    feature'lar yalnızca eğitim penceresinde, h dönem sonrasındaki hedefe göre taranır (Spearman, etkin örneklem,
    Benjamini–Hochberg, alt dönem istikrarı, redundancy eleme) ve seçilen feature'lar döner."""
    fcfg = FeatureConfig(target=target, horizon=horizon, train_end=_iso(train_end, "train_end") if train_end else None)
    pcfg = PrepConfig(target=target, train_end=fcfg.train_end) if prep else None
    result, _, panel = container().engineer_features(sector, fcfg, pcfg, _iso(start, "start"))
    return features_dict(result, sector, max_screening=60, skipped_panel=panel.skipped)


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def analyze_market_share(
    records: list[dict],
    focus: str | None = None,
    measure: str = "value",
    kind: str = "flow",
    unit: str = "",
    total_entity: str = "TOPLAM",
    basis: str = "auto",
    sector: str | None = None,
) -> dict:
    """Pazar payı analizi. records: [{entity, date, value, segment?}] (date: YYYY-AA, YYYY-Qn, YYYY ya da YYYY-AA-GG).
    measure: value (tutar/adet) | share (yüzde pay); kind: flow | stock. Pazar toplamı `total_entity` adlı satırlarla
    (varsayılan TOPLAM; TOTAL da kabul edilir) verilirse coverage oranı hesaplanır. Çıktı: paylar, HHI/CR-k, oyuncu
    bazında yıllık değişim ve eğilim, odak oyuncu için rakip adayları ve gerekçeleri, segment bazında shift-share
    ayrıştırması; sector verilirse pazar büyümesinin makro feature'larla lag'li korelasyonu."""
    config = ShareConfig(focus=focus, measure=measure, kind=kind, unit=unit[:40], total_entity=total_entity, basis=basis)
    return container().market_share.from_records(records, config, sector)


@server.tool(annotations=WRITES_LOCAL)
@safe_tool
def analyze_source_market_share(
    source: str,
    series: str,
    focus: str | None = None,
    start: str = "2015-01-01",
    end: str | None = None,
    sector: str | None = None,
    refresh: bool = False,
) -> dict:
    """Katalogdaki oyuncu/grup kırılımlı bir seriden pazar payı analizi; tablo vermeye gerek yoktur. Örnek: source=bddk,
    series=BDDK.AKTIF (aktif), BDDK.KREDI, BDDK.MEVDUAT; banka grupları (kamu, yerli özel ve yabancı mevduat bankaları,
    katılım, kalkınma ve yatırım) sektör toplamına göre paylaştırılır. *.SAHIPLIK serileri kamu / yerli özel / yabancı
    kırılımını verir. Veri yerel cache'ten okunur, eksikse kaynaktan çekilir (BDDK'da ay başına bir istek). Çıktı
    analyze_market_share ile aynıdır; meta.source kaynak atfını ve proxy durumunu taşır."""
    config = ShareConfig(focus=focus)
    first = _iso(start, "start")
    last = _iso(end, "end") if end else None
    return container().market_share.from_source(source, series, config, first, last, sector, refresh)


@server.tool(annotations=READ_REMOTE)
@safe_tool
def check_updates() -> dict:
    """Kurulu mlbooster sürümünü GitHub'daki en son sürümle karşılaştırır; güncelleme varsa nasıl yapılacağını bildirir.
    Sonucu kullanıcıya oturum başında bir kez göstermek yeterlidir."""
    return container().check_updates()


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="mlbooster-mcp %(levelname)s %(message)s")
    server.run()


if __name__ == "__main__":
    main()
