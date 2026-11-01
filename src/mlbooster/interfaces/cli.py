"""mlbooster CLI (typer). Yalnızca composition root'tan alınan use case'leri çağırır."""

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
from mlbooster.domain.services.backtest import MODELS, BacktestConfig
from mlbooster.domain.services.feature_engineering import FeatureConfig
from mlbooster.domain.services.market_share import ShareConfig
from mlbooster.domain.services.preprocessing import PrepConfig
from mlbooster.interfaces.presenters import (
    backtest_dict,
    features_dict,
    prep_dict,
    quality_dict,
    sector_map_dict,
    sector_map_markdown,
)

app = typer.Typer(help="ML Booster Map: Türkiye ücretsiz veri kaynakları → ML'e hazır panel", no_args_is_help=True)
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
    raise typer.BadParameter(f"Tarih YYYY-AA veya YYYY-AA-GG olmalı: {value}")


def _period_end(value: str | None) -> date | None:
    """`YYYY-AA` verilen dönem sınırları ayın son günü olarak yorumlanır (ör. 2022-12 → 2022-12-31)."""
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
    ready = container().providers.available()
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
    """Serileri listeler: datagroup verilirse kaynaktan canlı çeker, yoksa katalog tohumları."""
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
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG], varsayılan bugün")] = None,
    freq: Annotated[str | None, typer.Option(help="D,B,W,SM,M,Q,S,A")] = None,
    agg: Annotated[str | None, typer.Option(help="avg,min,max,first,last,sum")] = None,
    formula: Annotated[int, typer.Option(help="0 düzey, 1 %değişim, 3 yıllık %değişim, ...")] = 0,
):
    """Serileri çeker, yerel depoya yazar ve özet basar."""
    try:
        request = FetchRequest.create(source, series, _date(start), _date(end), freq, agg, formula)
        summary = container().fetch_series.summarize(request, max_rows=0)
    except DomainError as exc:
        _fail(exc)
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
    refresh: Annotated[bool, typer.Option(help="Yerel önbelleği yok sayıp yeniden çek")] = False,
    freq: Annotated[str, typer.Option(help="Panel frekansı: M (aylık), W (haftalık, Cuma), B (iş günü)")] = "M",
):
    """Sektörün feature'larından nokta-zamanlı gold panel üretir (aylık, haftalık ya da iş günlük)."""
    try:
        result = container().build_panel(sector, _date(start), _date(end), refresh, freq=freq)
        for name, reason in result.skipped.items():
            typer.secho(f"atlandı  {name}: {reason}", fg=typer.colors.YELLOW)
        if result.panel.empty:
            _fail(RuntimeError("Panel boş; atlanan feature'ları inceleyin."))
        location = result.location
        if out is not None:
            location = container().panels.export(result.panel, out)
    except (DomainError, ValueError) as exc:
        _fail(exc)
    unit = {"M": "ay", "W": "hafta", "B": "iş günü"}.get(freq, "dönem")
    typer.echo(f"{result.panel.shape[0]} {unit} × {result.panel.shape[1]} feature → {location}")


@app.command()
def health(source: Annotated[str | None, typer.Option(help="Tek kaynak")] = None):
    """Sağlık kontrolü (canary): her sağlayıcının kritik serilerinden son gözlemleri çeker."""
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
    limit: Annotated[int, typer.Option(help="En fazla sonuç")] = 20,
    as_json: Annotated[bool, typer.Option("--json", help="JSON çıktı")] = False,
):
    """Kaynakta veri grubu arar (Türkçe karakter/büyük-küçük harf duyarsız)."""
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
    feature: Annotated[str, typer.Option(help="Feature adı (map show çıktısındaki)")],
    series: Annotated[str, typer.Option(help="Seri kodu (canlı doğrulanır)")],
    name: Annotated[str | None, typer.Option(help="Seri açıklaması")] = None,
    kind: Annotated[str, typer.Option(help="level | flow")] = "level",
    lag: Annotated[int | None, typer.Option(help="Yayın gecikmesi (gün); verilmezse frekansa göre varsayılan")] = None,
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
        f"{b.sector_id}.{b.feature} ← {b.seed.code} (frekans {b.seed.freq.value}, gecikme {b.seed.publication_lag_days} gün, "
        f"son gözlem {b.last_observation}) → {b.location}"
    )


@app.command()
def audit(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="JSON raporu bu dosyaya yaz")] = None,
):
    """Sektör panelinin veri kalitesini denetler (güncellik, eksik gözlem, aykırı değer, yapısal kırılma, kolineerlik)."""
    try:
        report, result = container().audit_panel(sector, _date(start), _date(end))
    except DomainError as exc:
        _fail(exc)
    data = quality_dict(report, result.skipped)
    if json_out is not None:
        _write_json(data, json_out)
    counts = report.counts
    typer.echo(f"{report.months} ay × {len(report.series)} feature — yüksek {counts['high']}, orta {counts['medium']}, bilgi {counts['info']}")
    colors = {"high": typer.colors.RED, "medium": typer.colors.YELLOW, "info": typer.colors.BLUE}
    for i in report.issues:
        typer.secho(f"{i.severity.value:<7} {i.feature:<28} {i.message}", fg=colors[i.severity.value])


@app.command()
def backtest(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    target: Annotated[str, typer.Option(help="Hedef feature (panel kolonu)")],
    horizon: Annotated[int, typer.Option(help="Kaç ay ileri (1–24)")] = 1,
    min_train: Annotated[int, typer.Option(help="İlk eğitim penceresi (ay, ≥24)")] = 48,
    models: Annotated[str, typer.Option(help=f"Virgülle: {','.join(MODELS)}")] = ",".join(MODELS),
    no_exog: Annotated[bool, typer.Option("--no-exog", help="Ridge'de diğer feature'ları kullanma")] = False,
    anchor: Annotated[str | None, typer.Option(help="Çapa kolon (ör. aofm_son): anchor_naive modeli = bu kolonun son değeri")] = None,
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Metrik + tahminleri bu dosyaya yaz")] = None,
    panel_file: Annotated[Path | None, typer.Option("--panel-file", help="Sektör paneli yerine bu CSV'yi kullan (ör. features --selected-out çıktısı)")] = None,
    eval_start: Annotated[str | None, typer.Option(help="Metrikleri yalnızca bu aydan sonraki başlangıç noktalarında hesapla (seçim penceresi sonu)")] = None,
):
    """Rolling-origin backtest ile referans modelleri karşılaştırır (look-ahead korumalı)."""
    try:
        config = BacktestConfig(
            target=target, horizon=horizon, min_train=min_train,
            models=tuple(m.strip() for m in models.split(",") if m.strip()), exogenous=not no_exog, anchor=anchor,
            eval_start=pd.Timestamp(_period_end(eval_start)) if eval_start else None,
        )  # fmt: skip
        external = container().table_reader.read_panel(panel_file) if panel_file is not None else None
        result, _ = container().run_backtest(sector, config, _date(start), _date(end), panel=external)
    except DomainError as exc:
        _fail(exc)
    if json_out is not None:
        _write_json(backtest_dict(result, sector), json_out)
    typer.echo(f"Hedef {target}, ufuk {horizon} ay — en iyi: {result.best_model}")
    typer.echo(result.metrics.round(4).to_string(index=False))


def _prep_config(target, train_end, model, outliers, power, scaling, difference, max_gap) -> PrepConfig:
    return PrepConfig(target=target, train_end=_period_end(train_end), model_family=model, outliers=outliers, power=power,
                      scaling=scaling, difference=difference, max_gap=max_gap)  # fmt: skip


@app.command()
def prep(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    target: Annotated[str | None, typer.Option(help="Hedef kolon: yalnızca eksik doldurulur, dağılımı değiştirilmez")] = None,
    train_end: Annotated[str | None, typer.Option(help="Eğitim penceresinin son ayı (YYYY-AA); parametreler yalnızca bu pencereden tahmin edilir")] = None,
    model: Annotated[str, typer.Option(help="Hedeflenen model ailesi: linear | tree | neural")] = "linear",
    outliers: Annotated[str, typer.Option(help="auto | flag | winsorize | none")] = "auto",
    power: Annotated[str, typer.Option(help="auto | yeo_johnson | log | none")] = "auto",
    scaling: Annotated[str, typer.Option(help="auto | standard | robust | none")] = "auto",
    difference: Annotated[str, typer.Option(help="auto (birim kökte fark al) | none")] = "auto",
    max_gap: Annotated[int, typer.Option(help="İleri taşınacak en uzun boşluk (dönem)")] = 2,
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    out: Annotated[Path | None, typer.Option(help="Ön işlenmiş paneli bu .csv / .parquet yoluna yaz")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Profil ve planı bu JSON dosyasına yaz")] = None,
):
    """Dağılıma dayalı ön işleme: profil (ortalama/std ile medyan/MAD/IQR), plan ve sızıntısız uygulama."""
    try:
        config = _prep_config(target, train_end, model, outliers, power, scaling, difference, max_gap)
        result, panel = container().prepare_panel(sector, config, _date(start), _date(end))
        location = container().panels.export(result.panel, out) if out is not None else None
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
    target: Annotated[str | None, typer.Option(help="Hedef kolon: verilirse değişkenler h dönem sonrasına göre taranır ve seçilir")] = None,
    horizon: Annotated[int, typer.Option(help="Tahmin ufku (ay)")] = 1,
    train_end: Annotated[str | None, typer.Option(help="Eğitim penceresinin son ayı (YYYY-AA)")] = None,
    label: Annotated[str, typer.Option(help="Tarama etiketi: auto | level | change")] = "auto",
    max_features: Annotated[int | None, typer.Option(help="Seçilecek en fazla değişken (varsayılan: eğitim gözlemi / 10)")] = None,
    prep: Annotated[bool, typer.Option("--prep/--no-prep", help="Önce dağılıma dayalı ön işleme uygula")] = True,
    model: Annotated[str, typer.Option(help="Ön işleme için model ailesi: linear | tree | neural")] = "linear",
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    out: Annotated[Path | None, typer.Option(help="Bütün değişken matrisini bu .csv / .parquet yoluna yaz")] = None,
    selected_out: Annotated[Path | None, typer.Option("--selected-out", help="Hedef + seçilen değişkenleri yaz (backtest --panel-file için)")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Değişken kataloğu ve taramayı bu JSON dosyasına yaz")] = None,
):
    """Otomatik ve sektöre özgü değişken üretimi (gecikme, hareketli istatistik, tarif, takvim, olay) ve sızıntısız ön seçim."""
    try:
        fcfg = FeatureConfig(target=target, horizon=horizon, train_end=_period_end(train_end), label=label, max_features=max_features)
        pcfg = _prep_config(target, train_end, model, "auto", "auto", "auto", "auto", 2) if prep else None
        result, _, panel = container().engineer_features(sector, fcfg, pcfg, _date(start), _date(end))
        if out is not None:
            container().panels.export(result.features, out)
        if selected_out is not None:
            if not target:
                raise typer.BadParameter("--selected-out için --target gerekir")
            cols = list(dict.fromkeys([target, *result.selected]))
            container().panels.export(result.features[cols], selected_out)
    except (DomainError, ValueError) as exc:
        _fail(exc)
    data = features_dict(result, sector, skipped_panel=panel.skipped)
    if json_out is not None:
        _write_json(data, json_out)
    fam = ", ".join(f"{k} {v}" for k, v in data["families"].items())
    typer.echo(f"{data['n_features']} değişken üretildi ({fam})")
    for name, reason in result.skipped.items():
        typer.secho(f"atlandı  {name}: {reason}", fg=typer.colors.YELLOW)
    if target:
        typer.echo(f"Etiket: {result.label} · seçilen {len(result.selected)} değişken")
        for row in data["screening"][: max(len(result.selected), 5)]:
            mark = "✓" if row.get("selected") else " "
            r = row.get("r")
            typer.echo(f" {mark} {row['name']:<40} r={r:+.2f} q={row.get('q', float('nan')):.3f}  {row.get('reason') or ''}" if r is not None
                       else f"   {row['name']:<40} {row.get('reason') or ''}")  # fmt: skip


@app.command("market-share")
def market_share(
    input: Annotated[Path, typer.Option("--input", help="Pazar payı tablosu (.csv/.json): entity, date, value[, segment]")],
    focus: Annotated[str | None, typer.Option(help="Odak oyuncu (rakip seti bu oyuncuya göre çıkarılır)")] = None,
    measure: Annotated[str, typer.Option(help="value (tutar/adet) | share (yüzde pay)")] = "value",
    kind: Annotated[str, typer.Option(help="flow (satış, prim gibi dönemlik akım) | stock (abone, mevduat gibi stok)")] = "flow",
    unit: Annotated[str, typer.Option(help="Değer birimi (ör. adet, TL, abone)")] = "",
    total_entity: Annotated[str, typer.Option(help="Pazar toplamını taşıyan satırların oyuncu adı")] = "TOPLAM",
    basis: Annotated[str, typer.Option(help="auto | period (dönemlik) | ttm (son 12 ay toplamı)")] = "auto",
    trend_years: Annotated[int, typer.Option(help="Eğilim penceresi (yıl)")] = 3,
    top_n: Annotated[int, typer.Option(help="Grafikte ayrı gösterilecek oyuncu sayısı")] = 8,
    sector: Annotated[str | None, typer.Option(help="Sektör id: verilirse pazar büyümesi makro panelle ilişkilendirilir")] = None,
    start: Annotated[str, typer.Option(help="Makro panel başlangıcı, YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    json_out: Annotated[Path | None, typer.Option("--json", help="Analizi bu JSON dosyasına yaz")] = None,
):
    """Pazar payı analizi: paylar, yoğunlaşma (HHI, CR-k), oyuncu dinamikleri, rakip seti, segment ayrıştırması."""
    try:
        config = ShareConfig(focus=focus, measure=measure, kind=kind, total_entity=total_entity, basis=basis,
                             trend_years=trend_years, top_n=top_n, unit=unit)  # fmt: skip
        result = container().market_share.from_file(input, config, sector, _date(start))
    except DomainError as exc:
        _fail(exc)
    if json_out is not None:
        _write_json(result, json_out)
    meta, conc = result["meta"], result["concentration"]["latest"]
    typer.echo(f"Dönem {meta['first']} – {meta['last']} · taban: {meta['basis']} · {meta['entities']} oyuncu")
    typer.echo(f"HHI {conc['hhi']:,.0f} ({conc['class']}) · CR3 %{conc['cr3']:.1f} · eşdeğer oyuncu {conc['equivalent_players']:.1f}")
    for p in result["players"][:top_n]:
        d = p.get("d_year_pp")
        typer.echo(f"{p['rank']:>3}. {p['entity']:<28} %{p['share']:6.2f}  yıllık {d:+.2f} puan  {p['trend']}" if d is not None
                   else f"{p['rank']:>3}. {p['entity']:<28} %{p['share']:6.2f}  {p['trend']}")  # fmt: skip
    for i in result["issues"]:
        typer.secho(f"{i['severity']:<7} {i['message']}", fg=typer.colors.YELLOW if i["severity"] != "info" else typer.colors.BLUE)


@app.command()
def version(check: Annotated[bool, typer.Option("--check", help="GitHub'daki en son sürümle karşılaştır")] = False):
    """Kurulu sürümü gösterir; --check ile yeni sürüm olup olmadığını kontrol eder."""
    from mlbooster import __version__

    typer.echo(f"mlbooster {__version__}")
    if check:
        upd = container().check_updates()
        if upd["latest"] is None:
            typer.echo("En son sürüm alınamadı (çevrimdışı ya da MLBOOSTER_NO_UPDATE_CHECK=1).")
        elif upd["update_available"]:
            typer.secho(f"Yeni sürüm: v{upd['latest']}. {upd['how']}", fg=typer.colors.CYAN)
        else:
            typer.echo("Kurulu sürüm güncel.")


if __name__ == "__main__":
    app()
