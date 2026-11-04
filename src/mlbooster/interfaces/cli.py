"""mlbooster CLI (typer). Calls only the use cases obtained from the composition root.

Help and output text is user-facing and therefore written in Turkish."""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from mlbooster.application.dto import FetchRequest
from mlbooster.application.use_cases.panel import DEFAULT_START
from mlbooster.bootstrap import Container, build_container
from mlbooster.domain.errors import DomainError
from mlbooster.domain.model import HealthStatus
from mlbooster.domain.numfmt import tr_num
from mlbooster.domain.services.backtest import MODELS, BacktestConfig
from mlbooster.domain.services.feature_engineering import FeatureConfig
from mlbooster.domain.services.market_share import ShareConfig
from mlbooster.domain.services.preprocessing import PrepConfig
from mlbooster.interfaces.presenters import (
    BASIS_LABELS,
    HHI_CLASS_LABELS,
    SEVERITY_LABELS,
    TREND_LABELS,
    backtest_dict,
    features_dict,
    prep_dict,
    quality_dict,
    sector_map_dict,
    sector_map_markdown,
)

app = typer.Typer(help="ML Booster Map: Türkiye için ücretsiz veri kaynakları → ML'e hazır panel", no_args_is_help=True)
catalog_app = typer.Typer(help="Kaynak kataloğu", no_args_is_help=True)
map_app = typer.Typer(help="Sektör → kaynak haritası", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")
app.add_typer(map_app, name="map")


@cache
def container() -> Container:
    return build_container()


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise typer.BadParameter(f"Tarih YYYY-AA veya YYYY-AA-GG biçiminde olmalıdır: {value}")


def _period_end(value: str | None) -> date | None:
    """Period bounds given as `YYYY-MM` are read as the last day of the month (e.g. 2022-12 → 2022-12-31)."""
    if value is None:
        return None
    parsed = _date(value)
    if len(value.strip()) == 7:
        return (pd.Timestamp(parsed) + pd.offsets.MonthEnd(0)).date()
    return parsed


def _fail(exc: Exception) -> None:
    typer.secho(f"Hata: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@catalog_app.command("list")
def catalog_list(sector: Annotated[str | None, typer.Option(help="Sektör id (ör. bankacilik)")] = None):
    """Katalogdaki kaynakları listeler."""
    try:
        sources = container().list_sources(sector)
    except DomainError as exc:
        _fail(exc)
    ready = container().ready_providers()
    for s in sources:
        mark = "✓" if s.provider in ready else "—"
        typer.echo(f"{mark} {s.id:<8} {s.access.value:<14} {s.verification.value:<10} {s.name}")


@catalog_app.command("sectors")
def catalog_sectors():
    """Tanımlı sektörleri listeler."""
    for s in container().list_sectors():
        typer.echo(f"{s.id:<12} {s.name}")


@catalog_app.command("series")
def catalog_series(
    source: Annotated[str, typer.Option(help="Kaynak id")] = "evds3",
    datagroup: Annotated[str | None, typer.Option(help="EVDS veri grubu kodu (ör. bie_dkdovizgn)")] = None,
):
    """Serileri listeler: datagroup verilirse kaynaktan canlı çeker, verilmezse katalogdaki seed serileri döner."""
    try:
        df = container().discover_series(source, datagroup)
    except DomainError as exc:
        _fail(exc)
    typer.echo(df.to_string(index=False))


@map_app.command("show")
def map_show(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    format: Annotated[str, typer.Option(help="markdown | json")] = "markdown",
):
    """Bir sektörün veri kaynağı haritasını gösterir."""
    try:
        m = container().sector_map(sector)
    except DomainError as exc:
        _fail(exc)
    typer.echo(json.dumps(sector_map_dict(m), ensure_ascii=False, indent=2) if format == "json" else sector_map_markdown(m))


@app.command()
def fetch(
    series: Annotated[list[str], typer.Option(help="Seri kodu (birden fazla verilebilir)")],
    source: Annotated[str, typer.Option(help="Kaynak id")] = "evds3",
    start: Annotated[
        str | None,
        typer.Option(help="YYYY-AA[-GG]; verilmezse kaynağın varsayılan geçmişi (EPİAŞ: son 5 yıl), diğerleri 2010-01"),
    ] = None,
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG], varsayılan bugün")] = None,
    freq: Annotated[str | None, typer.Option(help="D,B,W,SM,M,Q,S,A")] = None,
    agg: Annotated[str | None, typer.Option(help="avg,min,max,first,last,sum")] = None,
    formula: Annotated[int, typer.Option(help="0 düzey, 1 yüzde değişim, 3 yıllık yüzde değişim, ...")] = 0,
    refresh: Annotated[bool, typer.Option(help="Yerel depoyu yok sayıp bütün aralığı kaynaktan yeniden çek")] = False,
):
    """Serileri çeker, yerel depoya yazar ve özetini gösterir.

    Artımlı kaynaklarda (EPİAŞ) yerel depoda bulunan dönemler yeniden çekilmez: yalnızca eksik geçmiş ve son
    günler kaynaktan istenir, istekler kaynağın tarih aralığı sınırına göre bölünür."""
    try:
        fetcher = container().fetch_series
        first = _date(start) if start else fetcher.default_start(source, DEFAULT_START)
        request = FetchRequest.create(source, series, first, _date(end), freq, agg, formula)
        summary = fetcher.summarize(request, max_rows=0, refresh=refresh)
    except DomainError as exc:
        _fail(exc)
    if summary.fetched:
        typer.echo("Kaynaktan çekilen dönem: " + ", ".join(f"{a} → {b}" for a, b in summary.fetched))
    else:
        typer.echo("Yerel depo güncel; kaynağa istek gönderilmedi.")
    if not summary.rows:
        typer.echo("Gözlem dönmedi.")
        return
    for row in summary.by_series:
        typer.echo(f"{row['series_id']:<20} {row['metric']:<12} {row['freq']:<3} {row['count']:>6}  {row['min']} → {row['max']}")
    typer.echo(summary.attribution)


@app.command("build-panel")
def build_panel(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    out: Annotated[Path | None, typer.Option(help="Ek olarak bu .parquet / .csv yoluna da yaz")] = None,
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    refresh: Annotated[bool, typer.Option(help="Yerel cache'i yok sayıp veriyi yeniden çek")] = False,
    freq: Annotated[str, typer.Option(help="Panel frekansı: M (aylık), W (haftalık, Cuma), B (iş günü)")] = "M",
):
    """Sektörün feature'larından point-in-time gold panel üretir (aylık, haftalık ya da iş günü frekansında)."""
    try:
        if out is not None:
            container().export_table.check(out)
        result = container().build_panel(sector, _date(start), _date(end), refresh, freq=freq)
        for name, reason in result.skipped.items():
            typer.secho(f"atlandı  {name}: {reason}", fg=typer.colors.YELLOW)
        if result.panel.empty:
            _fail(RuntimeError("Panel boş; atlanan feature'ları inceleyin."))
        location = result.location
        if out is not None:
            location = container().export_table(result.panel, out)
    except (DomainError, ValueError) as exc:
        _fail(exc)
    unit = {"M": "ay", "W": "hafta", "B": "iş günü"}.get(freq, "dönem")
    typer.echo(f"{result.panel.shape[0]} {unit} × {result.panel.shape[1]} feature → {location}")


@app.command()
def health(source: Annotated[str | None, typer.Option(help="Tek kaynak")] = None):
    """Healthcheck (canary): her provider'ın kritik serilerinden son gözlemleri çeker."""
    try:
        results = container().health(source)
    except DomainError as exc:
        _fail(exc)
    colors = {HealthStatus.OK: typer.colors.GREEN, HealthStatus.FAIL: typer.colors.RED, HealthStatus.SKIPPED: typer.colors.YELLOW}
    for r in results:
        typer.secho(f"{r.status.value:<8} {r.source_id:<8} {r.detail}", fg=colors[r.status])
    upd = container().check_updates()
    if upd["update_available"]:
        typer.secho(f"güncelleme v{upd['current']} → v{upd['latest']}: {upd['how']}", fg=typer.colors.CYAN)
    if any(r.status is HealthStatus.FAIL for r in results):
        raise typer.Exit(1)


def _write_json(data: dict, path: Path | None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if path is None:
        typer.echo(text)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        typer.echo(f"JSON → {path}", err=True)


@catalog_app.command("search")
def catalog_search(
    text: Annotated[str, typer.Argument(help="Aranacak kelimeler (ör. 'kredi faiz')")],
    source: Annotated[str, typer.Option(help="Kaynak id")] = "evds3",
    limit: Annotated[int, typer.Option(help="En fazla sonuç sayısı")] = 20,
    as_json: Annotated[bool, typer.Option("--json", help="Çıktıyı JSON olarak ver")] = False,
):
    """Kaynakta veri grubu arar (Türkçe karakter ve büyük/küçük harf farkı gözetilmez)."""
    try:
        df = container().search_series(text, source, limit)
    except DomainError as exc:
        _fail(exc)
    if as_json:
        typer.echo(df.to_json(orient="records", force_ascii=False, indent=2))
    elif df.empty:
        typer.echo("Sonuç yok.")
    else:
        for r in df.itertuples():
            tag = " [arşiv]" if r.archived else ""
            typer.echo(f"{r.datagroup:<24} {str(r.freq or ''):<3} {str(r.start or ''):>10} → {str(r.end or ''):<10} {r.name}{tag}")


@catalog_app.command("set-feature")
def catalog_set_feature(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    feature: Annotated[str, typer.Option(help="Feature adı (map show çıktısında görünen)")],
    series: Annotated[str, typer.Option(help="Seri kodu (canlı doğrulanır)")],
    name: Annotated[str | None, typer.Option(help="Seri açıklaması")] = None,
    kind: Annotated[str, typer.Option(help="level | flow")] = "level",
    lag: Annotated[int | None, typer.Option(help="Publication lag (gün); verilmezse frekansa göre varsayılan değer kullanılır")] = None,
    datagroup: Annotated[str | None, typer.Option(help="Veri grubu kodu")] = None,
    create: Annotated[bool, typer.Option("--create", help="Feature sektörde yoksa yeni feature olarak ekle")] = False,
    transform: Annotated[str, typer.Option(help="--create ile: level | diff | log_diff | yoy_pct")] = "level",
    agg: Annotated[str | None, typer.Option(help="--create ile, yüksek frekanslı seriler için: avg | last | sum")] = None,
):
    """Bir feature'a seri kodu bağlar ya da --create ile yeni feature ekler (~/.mlbooster/catalog/overrides.yaml)."""
    try:
        b = container().set_feature_series(
            sector, feature, series, name, kind, lag, datagroup, create=create, transform=transform, agg=agg
        )
    except DomainError as exc:
        _fail(exc)
    typer.echo(
        f"{b.sector_id}.{b.feature} ← {b.seed.code} (frekans {b.seed.freq.value}, publication lag {b.seed.publication_lag_days} gün, "
        f"son gözlem {b.last_observation}) → {b.location}"
    )


@app.command()
def audit(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="JSON raporu bu dosyaya yaz")] = None,
):
    """Sektör panelinin veri kalitesini denetler (stale seri, eksik gözlem, outlier, structural break, collinearity)."""
    try:
        report, result = container().audit_panel(sector, _date(start), _date(end))
    except DomainError as exc:
        _fail(exc)
    data = quality_dict(report, result.skipped)
    if json_out is not None:
        _write_json(data, json_out)
    counts = report.counts
    typer.echo(f"{report.months} ay × {len(report.series)} feature — kritik {counts['high']}, uyarı {counts['medium']}, bilgi {counts['info']}")
    colors = {"high": typer.colors.RED, "medium": typer.colors.YELLOW, "info": typer.colors.BLUE}
    for i in report.issues:
        label = SEVERITY_LABELS.get(i.severity.value, i.severity.value)
        typer.secho(f"{label:<7} {i.feature:<28} {i.message}", fg=colors[i.severity.value])


@app.command()
def backtest(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    target: Annotated[str, typer.Option(help="Hedef feature (panel kolonu)")],
    horizon: Annotated[int, typer.Option(help="Kaç ay ileri (1–24)")] = 1,
    min_train: Annotated[int, typer.Option(help="İlk eğitim penceresi (ay, ≥24)")] = 48,
    models: Annotated[str, typer.Option(help=f"Virgülle ayrılmış: {','.join(MODELS)}")] = ",".join(MODELS),
    no_exog: Annotated[bool, typer.Option("--no-exog", help="Ridge'de diğer feature'ları kullanma")] = False,
    anchor: Annotated[str | None, typer.Option(help="Anchor kolonu (ör. aofm_son): anchor_naive modeli bu kolonun son değerini kullanır")] = None,
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Metrikleri ve forecast'leri bu dosyaya yaz")] = None,
    panel_file: Annotated[Path | None, typer.Option("--panel-file", help="Sektör paneli yerine bu CSV'yi kullan (ör. features --selected-out çıktısı)")] = None,
    eval_start: Annotated[str | None, typer.Option(help="Metrikleri yalnızca bu aydan sonraki origin'lerde hesapla (seçim penceresinin sonu)")] = None,
):
    """Rolling-origin backtest ile baseline modelleri karşılaştırır (look-ahead korumalı)."""
    try:
        config = BacktestConfig(
            target=target, horizon=horizon, min_train=min_train,
            models=tuple(m.strip() for m in models.split(",") if m.strip()), exogenous=not no_exog, anchor=anchor,
            eval_start=pd.Timestamp(_period_end(eval_start)) if eval_start else None,
        )  # fmt: skip
        external = container().read_panel_file(panel_file) if panel_file is not None else None
        result, _ = container().run_backtest(sector, config, _date(start), _date(end), panel=external)
    except DomainError as exc:
        _fail(exc)
    if json_out is not None:
        _write_json(backtest_dict(result, sector), json_out)
    typer.echo(f"Hedef {target}, horizon {horizon} ay — en iyi: {result.best_model}")
    typer.echo(result.metrics.round(4).to_string(index=False))


def _prep_config(target, train_end, model, outliers, power, scaling, difference, max_gap) -> PrepConfig:
    return PrepConfig(target=target, train_end=_period_end(train_end), model_family=model, outliers=outliers, power=power,
                      scaling=scaling, difference=difference, max_gap=max_gap)  # fmt: skip


@app.command()
def prep(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    target: Annotated[str | None, typer.Option(help="Hedef kolon: yalnızca eksik değerleri doldurulur, dağılımı değiştirilmez")] = None,
    train_end: Annotated[str | None, typer.Option(help="Eğitim penceresinin son ayı (YYYY-AA); parametreler yalnızca bu pencereden tahmin edilir")] = None,
    model: Annotated[str, typer.Option(help="Hedeflenen model ailesi: linear | tree | neural")] = "linear",
    outliers: Annotated[str, typer.Option(help="auto | flag | winsorize | none")] = "auto",
    power: Annotated[str, typer.Option(help="auto | yeo_johnson | log | none")] = "auto",
    scaling: Annotated[str, typer.Option(help="auto | standard | robust | none")] = "auto",
    difference: Annotated[str, typer.Option(help="auto (unit root varsa fark al) | none")] = "auto",
    max_gap: Annotated[int, typer.Option(help="Forward-fill ile doldurulacak en uzun boşluk (dönem)")] = 2,
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    out: Annotated[Path | None, typer.Option(help="Ön işlenmiş paneli bu .csv / .parquet yoluna yaz")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Profil ve planı bu JSON dosyasına yaz")] = None,
):
    """Dağılıma dayalı ön işleme: profil (ortalama/std ile medyan/MAD/IQR), plan ve leakage'sız uygulama."""
    try:
        config = _prep_config(target, train_end, model, outliers, power, scaling, difference, max_gap)
        if out is not None:
            container().export_table.check(out)
        result, panel = container().prepare_panel(sector, config, _date(start), _date(end))
        location = container().export_table(result.panel, out) if out is not None else None
    except (DomainError, ValueError) as exc:
        _fail(exc)
    data = prep_dict(result, sector, panel.skipped)
    if json_out is not None:
        _write_json(data, json_out)
    for note in result.notes:
        typer.secho(note, fg=typer.colors.YELLOW)
    for col in data["columns"]:
        steps = " → ".join(s["step"] for s in col["steps"]) or "değişiklik yok"
        typer.echo(f"{col['feature']:<30} {col['before']['center_scale']:<24} {steps}")
    if location:
        typer.echo(f"Ön işlenmiş panel → {location}")


@app.command()
def features(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    target: Annotated[str | None, typer.Option(help="Hedef kolon: verilirse feature'lar h dönem sonrasındaki hedefe göre taranır ve seçilir")] = None,
    horizon: Annotated[int, typer.Option(help="Tahmin ufku (ay)")] = 1,
    train_end: Annotated[str | None, typer.Option(help="Eğitim penceresinin son ayı (YYYY-AA)")] = None,
    label: Annotated[str, typer.Option(help="Tarama etiketi: auto | level | change")] = "auto",
    max_features: Annotated[int | None, typer.Option(help="Seçilecek en fazla feature sayısı (varsayılan: eğitim gözlemi / 10)")] = None,
    prep: Annotated[bool, typer.Option("--prep/--no-prep", help="Önce dağılıma dayalı ön işleme uygula")] = True,
    model: Annotated[str, typer.Option(help="Ön işleme için model ailesi: linear | tree | neural")] = "linear",
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    out: Annotated[Path | None, typer.Option(help="Bütün feature matrisini bu .csv / .parquet yoluna yaz")] = None,
    selected_out: Annotated[Path | None, typer.Option("--selected-out", help="Hedefi ve seçilen feature'ları yaz (backtest --panel-file için)")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Feature kataloğunu ve tarama sonuçlarını bu JSON dosyasına yaz")] = None,
):
    """Otomatik ve sektöre özgü feature üretimi (lag, rolling istatistik, sektör tarifi, takvim, olay) ve leakage'sız ön seçim."""
    try:
        fcfg = FeatureConfig(target=target, horizon=horizon, train_end=_period_end(train_end), label=label, max_features=max_features)
        pcfg = _prep_config(target, train_end, model, "auto", "auto", "auto", "auto", 2) if prep else None
        if selected_out is not None and not target:
            raise typer.BadParameter("--selected-out için --target gerekir")
        for path in (out, selected_out):
            if path is not None:
                container().export_table.check(path)
        result, _, panel = container().engineer_features(sector, fcfg, pcfg, _date(start), _date(end))
        if out is not None:
            container().export_table(result.features, out)
        if selected_out is not None:
            cols = list(dict.fromkeys([target, *result.selected]))
            container().export_table(result.features[cols], selected_out)
    except (DomainError, ValueError) as exc:
        _fail(exc)
    data = features_dict(result, sector, skipped_panel=panel.skipped)
    if json_out is not None:
        _write_json(data, json_out)
    fam = ", ".join(f"{k} {v}" for k, v in data["families"].items())
    typer.echo(f"{data['n_features']} feature üretildi ({fam})")
    for name, reason in result.skipped.items():
        typer.secho(f"atlandı  {name}: {reason}", fg=typer.colors.YELLOW)
    if target:
        typer.echo(f"Etiket: {result.label} · {len(result.selected)} feature seçildi")
        for row in data["screening"][: max(len(result.selected), 5)]:
            mark = "✓" if row.get("selected") else " "
            r = row.get("r")
            typer.echo(f" {mark} {row['name']:<40} r={r:+.2f} q={row.get('q', float('nan')):.3f}  {row.get('reason') or ''}" if r is not None
                       else f"   {row['name']:<40} {row.get('reason') or ''}")  # fmt: skip


@app.command("market-share")
def market_share(
    input: Annotated[Path | None, typer.Option("--input", help="Pazar payı tablosu (.csv/.json): entity, date, value[, segment]")] = None,
    source: Annotated[str | None, typer.Option(help="--input yerine katalog kaynağı (ör. bddk); --series ile birlikte")] = None,
    series: Annotated[str | None, typer.Option(help="Oyuncu/grup kırılımlı katalog serisi (ör. BDDK.AKTIF, BDDK.KREDI)")] = None,
    refresh: Annotated[bool, typer.Option(help="--source ile: yerel cache'i yok sayıp veriyi yeniden çek")] = False,
    focus: Annotated[str | None, typer.Option(help="Odak oyuncu (rakip seti bu oyuncuya göre çıkarılır)")] = None,
    measure: Annotated[str, typer.Option(help="value (tutar/adet) | share (yüzde pay)")] = "value",
    kind: Annotated[str | None, typer.Option(help="flow (satış, prim gibi dönemlik akım) | stock (abone, mevduat gibi stok); varsayılan flow, --source ile katalogdaki seri türü")] = None,
    unit: Annotated[str, typer.Option(help="Değer birimi (ör. adet, TL, abone)")] = "",
    total_entity: Annotated[str, typer.Option(help="Pazar toplamını taşıyan satırların oyuncu adı (TOTAL da kabul edilir)")] = "TOPLAM",
    basis: Annotated[str, typer.Option(help="auto | period (dönemlik) | ttm (son 12 ay toplamı)")] = "auto",
    trend_years: Annotated[int, typer.Option(help="Trend penceresi (yıl)")] = 3,
    top_n: Annotated[int, typer.Option(help="Grafikte ayrı gösterilecek oyuncu sayısı")] = 8,
    sector: Annotated[str | None, typer.Option(help="Sektör id: verilirse pazar büyümesi makro panelle ilişkilendirilir")] = None,
    start: Annotated[str, typer.Option(help="Makro panel ve --source verisinin başlangıcı, YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="--source ile: bitiş, YYYY-AA[-GG], varsayılan bugün")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Analizi bu JSON dosyasına yaz")] = None,
):
    """Pazar payı analizi: paylar, yoğunlaşma (HHI, CR-k), oyuncu dinamikleri, rakip seti, segment bazında shift-share ayrıştırması.
    Girdi bir tablo (--input) ya da oyuncu/grup kırılımlı bir katalog serisidir (--source bddk --series BDDK.AKTIF)."""
    if (input is None) == (source is None) or (source is None) != (series is None):
        _fail(ValueError("--input ya da --source ile --series birlikte verilmelidir (ikisi aynı anda değil)"))
    try:
        config = ShareConfig(focus=focus, measure=measure, kind=kind or "flow", total_entity=total_entity, basis=basis,
                             trend_years=trend_years, top_n=top_n, unit=unit)  # fmt: skip
        if source is not None:
            typer.echo(f"{source} kaynağından {series} çekiliyor (yerel cache kullanılır; ilk çekim birkaç dakika sürebilir)", err=True)
            result = container().market_share.from_source(source, series, config, _date(start), _date(end), sector, refresh, kind)
        else:
            result = container().market_share.from_file(input, config, sector, _date(start))
    except DomainError as exc:
        _fail(exc)
    if json_out is not None:
        _write_json(result, json_out)
    meta, conc = result["meta"], result["concentration"]["latest"]
    if src := meta.get("source"):
        typer.echo(f"{src['name']} · {src['attribution']}")
    typer.echo(f"Dönem {meta['first']} – {meta['last']} · taban: {BASIS_LABELS.get(meta['basis'], meta['basis'])} · {meta['entities']} oyuncu")
    typer.echo(f"HHI {tr_num(conc['hhi'], ',.0f')} ({HHI_CLASS_LABELS.get(conc['class'], conc['class'])}) · CR3 %{tr_num(conc['cr3'], '.1f')} · "
               f"eşdeğer oyuncu {tr_num(conc['equivalent_players'], '.1f')}")
    for p in result["players"][:top_n]:
        d, share = p.get("d_year_pp"), "%" + tr_num(p["share"], ".2f")
        trend = TREND_LABELS.get(p["trend"], p["trend"])
        horizon = f"{trend_years} yıllık trend: {trend}"
        typer.echo(f"{p['rank']:>3}. {p['entity']:<28} {share:>7}  son 1 yıl {tr_num(d, '+.2f')} puan · {horizon}" if d is not None
                   else f"{p['rank']:>3}. {p['entity']:<28} {share:>7}  {horizon}")  # fmt: skip
    for i in result["issues"]:
        typer.secho(f"{SEVERITY_LABELS.get(i['severity'], i['severity']):<7} {i['message']}", fg=typer.colors.YELLOW if i["severity"] != "info" else typer.colors.BLUE)


@app.command()
def version(check: Annotated[bool, typer.Option("--check", help="GitHub'daki en son sürümle karşılaştır")] = False):
    """Kurulu sürümü gösterir; --check ile yeni sürüm olup olmadığını kontrol eder."""
    from mlbooster import __version__

    typer.echo(f"mlbooster {__version__}")
    if check:
        upd = container().check_updates()
        if upd["latest"] is None:
            typer.echo("En son sürüm bilgisi alınamadı (çevrimdışı ya da MLBOOSTER_NO_UPDATE_CHECK=1).")
        elif upd["update_available"]:
            typer.secho(f"Yeni sürüm: v{upd['latest']}. {upd['how']}", fg=typer.colors.CYAN)
        else:
            typer.echo("Kurulu sürüm güncel.")


def _free_port(start: int, attempts: int = 20) -> int | None:
    """First port on 127.0.0.1, starting at `start`, that can be bound."""
    import socket

    for port in range(start, min(start + attempts, 65536)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return None


@app.command()
def web(
    port: Annotated[int, typer.Option(min=1024, max=65535, help="Yerel port")] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Tarayıcıda otomatik aç")] = True,
):
    """Yerel web panelini başlatır. Yalnızca bu bilgisayardan erişilebilir (127.0.0.1); durdurmak için Ctrl+C."""
    import threading
    import webbrowser

    import uvicorn

    from mlbooster.interfaces.web import create_app

    free = _free_port(port)
    if free is None:
        _fail(Exception(f"{port}–{port + 19} aralığında boş port yok; --port ile başka bir port verin."))
    if free != port:
        typer.echo(f"{port} numaralı port kullanımda; {free} kullanılıyor.")
    port = free
    url = f"http://127.0.0.1:{port}/"
    typer.echo(f"ML Booster paneli: {url}  (durdurmak için Ctrl+C)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(create_app(container), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    app()
